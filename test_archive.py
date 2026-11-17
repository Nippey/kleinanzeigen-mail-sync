import unittest,tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import archive
class API:
    def __init__(self): self.calls=0; self.latest='1'
    def conversations(self): return [{'id':'c','raw':{'receivedDate':'date','unichatLatestMessage':{'id':self.latest}}}]
    def messages(self,cid):
        self.calls+=1
        return [{'raw':{'messageId':self.latest,'attachments':[{'imageURL':'https://img.kleinanzeigen.de/f'}]}}]
class Test(unittest.TestCase):
    def test_incremental(self):
        api=API(); db=archive.db_open(':memory:')
        with tempfile.TemporaryDirectory() as tmp, patch('archive.time.sleep'), patch('archive.download_attachment',return_value='abc'):
            folder=Path(tmp); (folder/'abc').write_bytes(b'x')
            archive.backup(db,api,folder); archive.backup(db,api,folder)
            self.assertEqual(api.calls,1)
            api.latest='2'; archive.backup(db,api,folder)
            self.assertEqual(api.calls,2)
            db.execute('UPDATE chat_state SET checked=0'); archive.backup(db,api,folder)
            self.assertEqual(api.calls,3)
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],2)
    def test_reject_host(self):
        with self.assertRaises(ValueError): archive.download_attachment('https://example.com/a',Path('/tmp'),None)
    def test_authenticated_download(self):
        api=MagicMock(); api._headers.return_value={"Authorization":"test"}
        response=api._s.stream.return_value.__enter__.return_value
        response.status_code=200; response.headers={"Content-Type":"image/jpeg","Content-Length":"3"}
        response.iter_content.return_value=[b"abc"]
        with tempfile.TemporaryDirectory() as tmp:
            key=archive.download_attachment('https://gateway.kleinanzeigen.de/a',Path(tmp),api)
            self.assertEqual((Path(tmp)/key).read_bytes(),b"abc")
            api._headers.assert_called_once_with(authed=True,gateway=True)
            self.assertFalse(api._s.stream.call_args.kwargs['allow_redirects'])
            response.status_code=302
            with self.assertRaises(archive.AttachmentHTTPError):
                archive.download_attachment('https://api.kleinanzeigen.de/a',Path(tmp),api)
    def test_pending_retry(self):
        api=API(); db=archive.db_open(':memory:')
        with tempfile.TemporaryDirectory() as tmp, patch('archive.time.sleep'), patch('archive.fetch_attachment',return_value=None):
            archive.backup(db,api,Path(tmp)); archive.backup(db,api,Path(tmp))
            self.assertEqual(api.calls,2)
if __name__=='__main__': unittest.main()
