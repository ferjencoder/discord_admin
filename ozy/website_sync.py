"""Persistent retries for bot-created website communications, using existing state backups."""
import asyncio
import json

from ozy.data_provider import DataUnavailable


class WebsiteSync:
    KEY = 'website_sync_pending'
    OPERATIONS = {'upsert_announcement', 'delete_announcement', 'upsert_schedule_event', 'delete_schedule_event'}

    def __init__(self, state, provider, on_pending=None):
        self.state, self.provider = state, provider
        self.on_pending = on_pending
        self.lock = asyncio.Lock()

    async def submit(self, operation, payload):
        if operation not in self.OPERATIONS:
            raise ValueError('Unsupported website operation')
        async with self.lock:
            pending = json.loads(self.state.get_value(self.KEY) or '{}')
            identifier = str(payload['id'] if isinstance(payload, dict) else payload)
            kind = 'announcement' if 'announcement' in operation else 'schedule'
            key = f'{kind}:{identifier}'
            previous = pending.get(key)
            if previous and operation.startswith('upsert') and previous['operation'] == operation:
                payload = {**previous['payload'], **payload}
            pending[key] = {'operation': operation, 'payload': payload}
            self.state.set_value(self.KEY, json.dumps(pending))
            try:
                result = await getattr(self.provider, operation)(payload)
            except Exception:
                if self.on_pending:
                    self.on_pending()
                raise
            pending.pop(key)
            self.state.set_value(self.KEY, json.dumps(pending))
            return result

    async def retry(self):
        async with self.lock:
            pending = json.loads(self.state.get_value(self.KEY) or '{}')
            for key, item in list(pending.items()):
                try:
                    await getattr(self.provider, item['operation'])(item['payload'])
                except DataUnavailable:
                    continue
                pending.pop(key)
                self.state.set_value(self.KEY, json.dumps(pending))
            return len(pending)
