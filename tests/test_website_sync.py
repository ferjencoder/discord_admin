import asyncio
from ozy.website_sync import WebsiteSync
from ozy.data_provider import DataUnavailable
from ozy.state import AdminState

class Provider:
    def __init__(self):
        self.offline = True
        self.calls = []
    async def upsert_announcement(self, payload):
        if self.offline: raise DataUnavailable('offline')
        self.calls.append(('post', payload))
        return {'ok': True}
    async def delete_announcement(self, payload):
        if self.offline: raise DataUnavailable('offline')
        self.calls.append(('delete', payload))
        return True

def test_restart_recovers_failed_publication(tmp_path):
    async def run():
        path=tmp_path/'state.sqlite3'
        state=AdminState(path)
        provider=Provider()
        sync=WebsiteSync(state, provider)
        try: await sync.submit('upsert_announcement', {'id':'12345','title':'News'})
        except DataUnavailable: pass
        recovered=WebsiteSync(AdminState(path), provider)
        assert await recovered.retry() == 1
        provider.offline=False
        assert await recovered.retry() == 0
        assert provider.calls == [('post', {'id':'12345','title':'News'})]
        assert await recovered.retry() == 0
        assert len(provider.calls) == 1
    asyncio.run(run())

def test_delete_supersedes_failed_create(tmp_path):
    async def run():
        provider=Provider()
        sync=WebsiteSync(AdminState(tmp_path/'state.sqlite3'), provider)
        for op,payload in [('upsert_announcement',{'id':'12345'}),('delete_announcement','12345')]:
            try: await sync.submit(op,payload)
            except DataUnavailable: pass
        provider.offline=False
        assert await sync.retry() == 0
        assert provider.calls == [('delete','12345')]
    asyncio.run(run())
