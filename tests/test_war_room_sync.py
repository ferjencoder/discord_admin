import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from datetime import datetime, timezone
import discord
from ozy.war_room_sync import WarRoomSync, event_description

def missing():
    return discord.NotFound(SimpleNamespace(status=404,reason='Not found'),{'message':'Not found','code':100})

class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        state_values,events,messages={},[],[]
        state=SimpleNamespace(get_value=lambda key:state_values.get(key),set_value=lambda key,value:state_values.__setitem__(key,value),delete_event_reminders=Mock(),reschedule_event_reminders=Mock())
        guild=SimpleNamespace(id=123456)
        counts={'create':0,'send':0,'edit':0,'post_edit':0,'history':0}
        failures={'create':False,'send':False}
        async def create(guild_id,**payload):
            counts['create']+=1
            event=SimpleNamespace(id=223456,description=payload['description'],status=discord.EventStatus.scheduled,entity_type=discord.EntityType.external)
            async def edit(**kw):
                counts['edit']+=1
                for key,value in kw.items():setattr(event,key,value)
            async def delete(**kw):events.remove(event)
            event.edit,event.delete=edit,delete
            events.append(event)
            if failures['create']:
                failures['create']=False
                raise TimeoutError('response lost after Discord accepted event')
            return {'id':event.id}
        async def fetch_event(id,**kwargs):
            if not events:raise missing()
            return events[0]
        async def fetch_events():return events
        guild._state=SimpleNamespace(http=SimpleNamespace(create_guild_scheduled_event=create))
        guild.fetch_scheduled_event,guild.fetch_scheduled_events=fetch_event,fetch_events
        channel=SimpleNamespace(id=323456,guild=guild)
        async def send(**kwargs):
            counts['send']+=1
            message=SimpleNamespace(id=423456,author=SimpleNamespace(id=523456),embeds=[kwargs['embed']])
            async def edit(**kwargs):counts['post_edit']+=1;message.embeds=[kwargs['embed']]
            async def delete():messages.remove(message)
            message.edit,message.delete=edit,delete
            messages.append(message)
            if failures['send']:
                failures['send']=False
                raise TimeoutError('response lost after Discord accepted message')
            return message
        async def fetch_message(id):
            if not messages:raise missing()
            return messages[0]
        async def history(**kwargs):
            counts["history"]+=1
            self.assertEqual(kwargs["limit"],200)
            for message in messages:yield message
        channel.send,channel.fetch_message,channel.history=send,fetch_message,history
        guild.get_channel_or_thread=lambda id:channel
        bot=SimpleNamespace(state=state,user=SimpleNamespace(id=523456),get_guild=lambda id:guild,
            settings=SimpleNamespace(server_id=guild.id,schedule_channel_id=channel.id,schedule_url='https://ozy.com.ar/api/ozy/schedule',ozy_data_api_token='test'))
        item={'id':'web-abc','version':1,'status':'open','title':'CP run','description':'Notes','location':'K:233','capacity':2,'start_utc':'2099-10-05T17:00:00Z','end_utc':'2099-10-05T18:00:00Z'}
        return WarRoomSync(bot),item,counts,failures,events,messages

    async def test_create_edit_delete_reuse_original_ids_and_post(self):
        bridge,item,counts,failures,events,messages=self.fixture()
        link=await bridge.apply(item)
        self.assertEqual(counts['history'],0)
        item.update(title='Updated CP',version=2,discord=link)
        second=await bridge.apply(item)
        self.assertEqual(link,second);self.assertEqual(counts['create'],1);self.assertEqual(counts['send'],1)
        self.assertEqual(messages[0].embeds[0].title,'Updated CP')
        item.update(status='deleted',version=3)
        await bridge.apply(item);await bridge.apply(item)
        self.assertFalse(events);self.assertFalse(messages)
        bridge.bot.state.delete_event_reminders.assert_called_with(223456)

    async def test_timeout_after_event_creation_does_not_duplicate(self):
        bridge,item,counts,failures,events,messages=self.fixture()
        failures['create']=True
        with self.assertRaises(TimeoutError):await bridge.apply(item)
        bridge=WarRoomSync(bridge.bot)
        await bridge.apply(item)
        self.assertEqual(counts['create'],1);self.assertEqual(counts['send'],1)

    async def test_timeout_after_post_creation_does_not_duplicate(self):
        bridge,item,counts,failures,events,messages=self.fixture()
        failures['send']=True
        with self.assertRaises(TimeoutError):await bridge.apply(item)
        bridge=WarRoomSync(bridge.bot)
        await bridge.apply(item)
        self.assertEqual(counts['create'],1);self.assertEqual(counts['send'],1);self.assertEqual(counts['post_edit'],1)

    async def test_failed_permissions_do_not_ack_success(self):
        bridge,item,counts,failures,events,messages=self.fixture()
        calls=[]
        async def api(body=None):
            if body is None:return {'events':[item]}
            calls.append(body);return {'ok':True}
        async def apply(item):raise PermissionError('No edit permission')
        bridge.api,bridge.apply=api,apply
        await bridge.run()
        self.assertEqual(calls[0]['action'],'claim')
        self.assertIn('error',calls[1]);self.assertEqual(calls[0]['lease'],calls[1]['lease'])
        self.assertNotIn('discord',calls[1])

    async def test_cancel_updates_post_and_does_not_create_another_event(self):
        bridge,item,counts,failures,events,messages=self.fixture()
        await bridge.apply(item)
        item.update(status='cancelled',version=2)
        await bridge.apply(item)
        self.assertEqual(events[0].status,discord.EventStatus.cancelled)
        self.assertEqual(counts['create'],1);self.assertEqual(counts['send'],1)
        self.assertEqual(messages[0].embeds[0].fields[2].value,'Cancelled')

    def test_description_marker_is_removed_from_member_content(self):
        self.assertEqual(event_description('Notes\nOZY-WAR-ID:web-abc'),'Notes')

    async def test_completion_updates_original_post_without_deleting_or_recreating(self):
        bridge,item,counts,failures,events,messages=self.fixture()
        await bridge.apply(item)
        events[0].status=discord.EventStatus.active
        item.update(status='completed',version=2)
        await bridge.apply(item)
        self.assertEqual(events[0].status,discord.EventStatus.completed)
        self.assertEqual(counts['create'],1)
        self.assertEqual(counts['send'],1)
        self.assertEqual(messages[0].embeds[0].fields[2].value,'Ended')

    async def test_terminal_gateway_updates_send_status_not_destructive_deletion(self):
        from unittest.mock import AsyncMock
        from bot import OZYAdminBot
        fake=SimpleNamespace(settings=SimpleNamespace(server_id=123456),data=object(),website_sync=SimpleNamespace(submit=AsyncMock()))
        for status in (discord.EventStatus.cancelled,discord.EventStatus.completed):
            after=SimpleNamespace(id=223456,guild_id=123456,status=status,start_time=datetime(2026,10,4,17,tzinfo=timezone.utc),end_time=datetime(2026,10,4,18,tzinfo=timezone.utc),name='Fixture only',description='Notes',location='Test',channel=None,url='https://discord.com/events/123456/223456')
            await OZYAdminBot.on_scheduled_event_update(fake,None,after)
            operation,payload=fake.website_sync.submit.call_args.args
            self.assertEqual(operation,'upsert_schedule_event')
            self.assertEqual(payload['status'],status.name)

    async def test_retry_backoff_skips_repeated_failed_work_but_new_version_retries(self):
        from unittest.mock import AsyncMock
        bridge,item,counts,failures,events,messages=self.fixture()
        async def api(body=None):
            return {'events':[item]} if body is None else {'ok':True}
        bridge.api=api
        bridge.apply=AsyncMock(side_effect=PermissionError('Fixture permission failure'))
        await bridge.run()
        await bridge.run()
        self.assertEqual(bridge.apply.await_count,1)
        item['version']=2
        await bridge.run()
        self.assertEqual(bridge.apply.await_count,2)
