import io
import unittest
from unittest.mock import Mock
from app.bambu import ImplicitFTPS

class TransferTests(unittest.TestCase):
    def test_upload_closes_data_socket_and_requires_final_response(self):
        ftp=ImplicitFTPS(Mock())
        connection=Mock()
        context=Mock();context.__enter__=Mock(return_value=connection);context.__exit__=Mock(return_value=False)
        ftp.voidcmd=Mock();ftp.transfercmd=Mock(return_value=context);ftp.voidresp=Mock(return_value='226 Transfer complete')
        self.assertEqual(ftp.storbinary('STOR test',io.BytesIO(b'abcdefgh'),blocksize=3),'226 Transfer complete')
        self.assertEqual(b''.join(call.args[0] for call in connection.sendall.call_args_list),b'abcdefgh')
        connection.unwrap.assert_not_called()
        context.__exit__.assert_called_once()
        ftp.voidresp.assert_called_once()
    def test_failed_final_response_is_not_upload_success(self):
        ftp=ImplicitFTPS(Mock());context=Mock();context.__enter__=Mock(return_value=Mock());context.__exit__=Mock(return_value=False)
        ftp.voidcmd=Mock();ftp.transfercmd=Mock(return_value=context);ftp.voidresp=Mock(side_effect=OSError('transfer rejected'))
        with self.assertRaises(OSError):ftp.storbinary('STOR test',io.BytesIO(b'abc'))

if __name__=='__main__':unittest.main()
