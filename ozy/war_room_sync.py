"""Apply versioned website event changes to the original Discord event and post.

The website retains pending work until this worker acknowledges both writes.
Local mappings and stable markers recover creates after interrupted responses.
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import discord

log = logging.getLogger(__name__)
MARKER = "OZY-WAR-ID:"

def event_description(text):
    return (text or '').split('\n' + MARKER, 1)[0].strip()

class WarRoomSync:
    KEY = 'war_room_discord_links'

    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.retry_after = {}

    async def api(self, body=None):
        settings = self.bot.settings
        parsed = urlsplit(settings.schedule_url)
        url = urlunsplit((parsed.scheme, parsed.netloc, '/.netlify/functions/war-room-sync', '', ''))
        headers = {'X-OZY-Admin-Token': settings.ozy_data_api_token}
        async with self.bot.data.session.request('POST' if body else 'GET', url,
                json=body, headers=headers, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status != 200:
                raise RuntimeError(f'War Room sync returned HTTP {response.status}')
            return await response.json()

    def save(self, links):
        self.bot.state.set_value(self.KEY, json.dumps(links))

    async def run(self):
        if not self.bot.settings.schedule_url or not self.bot.settings.ozy_data_api_token:
            return 0
        async with self.lock:
            pending = await self.api()
            active_ids = {e['id'] for e in pending.get('events', [])}
            self.retry_after = {k:v for k,v in self.retry_after.items() if k in active_ids}
            for event in pending.get('events', []):
                prior = self.retry_after.get(event['id'])
                if prior and prior['version'] == event['version'] and prior['until'] > time.monotonic():
                    continue
                lease = str(uuid.uuid4())
                try:
                    await self.api({'action':'claim','id':event['id'],'version':event['version'],'lease':lease})
                    link = await self.apply(event)
                    await self.api({'id': event['id'], 'version': event['version'], 'discord': link,'lease':lease})
                    self.retry_after.pop(event['id'], None)
                except Exception as exc:
                    attempts = prior['attempts'] + 1 if prior and prior['version'] == event['version'] else 1
                    self.retry_after[event['id']] = {'version':event['version'],'attempts':attempts,'until':time.monotonic()+min(3600,60*2**min(attempts-1,6))}
                    log.warning('War Room sync failed for %s: %s', event.get('id'), exc)
                    try:
                        await self.api({'id': event['id'], 'version': event['version'],
                            'lease':lease,'error': 'Discord update failed. Check bot permissions and schedule channel.'})
                    except Exception:
                        log.warning('Could not acknowledge War Room sync failure')

            return len(self.retry_after)

    async def apply(self, item):
        bot = self.bot
        guild = bot.get_guild(bot.settings.server_id)
        if guild is None:
            raise RuntimeError('Configured guild is unavailable')
        links = json.loads(bot.state.get_value(self.KEY) or '{}')
        link = {**item.get('discord', {}), **links.get(item['id'], {})}
        if link.get('guild_id') and int(link['guild_id']) != guild.id:
            raise RuntimeError('Event belongs to another guild')
        link['guild_id'] = str(guild.id)
        channel_id = link.get('channel_id') or bot.settings.schedule_channel_id
        if not channel_id:
            raise RuntimeError('SCHEDULE_CHANNEL_ID is required')
        channel = guild.get_channel_or_thread(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        if getattr(channel, 'guild', None) is None or channel.guild.id != guild.id:
            raise RuntimeError('Publish channel belongs to another guild')
        link['channel_id'] = str(channel.id)
        marker = MARKER + item['id']
        event = None
        if link.get('event_id'):
            try:
                event = await guild.fetch_scheduled_event(int(link['event_id']))
            except discord.NotFound:
                pass
        else:
            # Never repeat a create blindly after a failed/ambiguous response.
            for candidate in await guild.fetch_scheduled_events():
                if marker in (candidate.description or ''):
                    event = candidate
                    link['event_id'] = str(candidate.id)
                    break
        message = None
        if link.get('message_id'):
            try:
                message = await channel.fetch_message(int(link['message_id']))
            except discord.NotFound:
                pass
        elif link.get('attempt_started'):
            after = datetime.fromisoformat(link['attempt_started']) - timedelta(minutes=1) if link.get('attempt_started') else None
            searched = 0
            async for candidate in channel.history(limit=200, after=after):
                searched += 1
                if candidate.author.id == bot.user.id and any(marker == embed.footer.text for embed in candidate.embeds):
                    message = candidate
                    link['message_id'] = str(candidate.id)
                    break
            if message is None and searched >= 200:
                raise RuntimeError('Post recovery is ambiguous; refusing a duplicate announcement')
        if item['status'] == 'deleted':
            if event:
                await event.delete(reason='Deleted in OZY War Room')
            if message:
                if message.author.id != bot.user.id:
                    raise RuntimeError('Announcement is not owned by OZY Admin')
                await message.delete()
            if link.get('event_id'):
                bot.state.delete_event_reminders(int(link['event_id']))
            links[item['id']] = link
            self.save(links)
            return {k:v for k,v in link.items() if k in ('guild_id','event_id','channel_id','message_id')}
        start = datetime.fromisoformat(item['start_utc'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(item['end_utc'].replace('Z', '+00:00'))
        description = (item.get('description', '')[:900] + '\n' + marker) if item['id'].startswith('web-') else item.get('description', '')[:1000]
        if item['status'] in ('cancelled', 'completed'):
            if event and event.status not in (discord.EventStatus.cancelled, discord.EventStatus.completed):
                if event.status == discord.EventStatus.active:
                    await event.edit(status=discord.EventStatus.completed)
                else:
                    await event.edit(status=discord.EventStatus.cancelled)
            if link.get('event_id'):
                bot.state.delete_event_reminders(int(link['event_id']))
        elif event:
            kwargs = dict(name=item['title'], description=description, end_time=end,
                          reason='Updated in OZY War Room')
            if event.status == discord.EventStatus.scheduled:
                kwargs['start_time'] = start
            if event.entity_type == discord.EntityType.external:
                kwargs['location'] = (item.get('location') or 'OZY War Room')[:100]
            await event.edit(**kwargs)
        else:
            if link.get('event_id'):
                raise RuntimeError('Linked Discord event was deleted; refusing to recreate it')
            if start <= datetime.now(timezone.utc):
                raise RuntimeError('Cannot publish a new Discord event after its start time')
            payload = dict(name=item['title'], description=description, scheduled_start_time=start.isoformat(),
                scheduled_end_time=end.isoformat(), entity_type=3, privacy_level=2,
                entity_metadata={'location': (item.get('location') or 'OZY War Room')[:100]})
            created = await guild._state.http.create_guild_scheduled_event(guild.id, reason='OZY War Room', **payload)
            link['event_id'] = str(created['id'])
            links[item['id']] = link
            self.save(links)
            event = await guild.fetch_scheduled_event(int(created['id']))
        if link.get('event_id') and item['status'] not in ('cancelled', 'completed'):
            bot.state.reschedule_event_reminders(int(link['event_id']), start)
        embed = discord.Embed(title=item['title'], description=item.get('description') or 'No notes.', color=0xEF8634)
        embed.add_field(name='Starts', value=f'<t:{int(start.timestamp())}:F>\n<t:{int(start.timestamp())}:R>')
        embed.add_field(name='Duration', value=f"{round((end-start).total_seconds()/60)} min")
        embed.add_field(name='Status', value={'cancelled':'Cancelled','completed':'Ended','closed':'Sign-ups closed'}.get(item['status'],'Open for sign-ups'))
        embed.add_field(name='Meeting point', value=item.get('location') or 'OZY War Room')
        embed.add_field(name='Places', value=str(item.get('capacity') or 'Unlimited'))
        embed.set_footer(text=marker)
        view = discord.ui.View(timeout=None)
        origin = urlsplit(bot.settings.schedule_url)
        view.add_item(discord.ui.Button(label='War Room · details & sign-up', style=discord.ButtonStyle.link,
            url=urlunsplit((origin.scheme,origin.netloc,'/war-room','',item['id']))))
        if link.get('event_id') and item['status'] not in ('cancelled', 'completed'):
            view.add_item(discord.ui.Button(label='Discord event',style=discord.ButtonStyle.link,
                url=f"https://discord.com/events/{guild.id}/{link['event_id']}"))
        if message:
            if message.author.id != bot.user.id:
                raise RuntimeError('Announcement is not owned by OZY Admin')
            await message.edit(embed=embed,view=view,allowed_mentions=discord.AllowedMentions.none())
        else:
            # Persist the search window before sending, so a retry can recover
            # a message delivered despite a transport timeout.
            link.setdefault('attempt_started',datetime.now(timezone.utc).isoformat())
            links[item['id']] = link
            self.save(links)
            message = await channel.send(embed=embed,view=view,allowed_mentions=discord.AllowedMentions.none())
            link['message_id'] = str(message.id)
        links[item['id']] = link
        self.save(links)
        return {k:v for k,v in link.items() if k in ('guild_id','event_id','channel_id','message_id')}
