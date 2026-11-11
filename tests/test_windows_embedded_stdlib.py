"""Source-only ZIP layout regression; fixtures are never executable runtimes."""
import unittest
import zipfile
import tests.test_windows_lifecycle_launcher as fixtures
build=fixtures.build


class EmbeddedStdlibTests(unittest.TestCase):
    def test_source_and_official_compiled_entry_pass_but_missing_encodings_fails(self):
        fixture=fixtures.BuilderInputTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        package=fixture.package
        (package/'runtime/python.exe').write_bytes(b'MZ synthetic source-test only; never executed')
        for entry in ('encodings/__init__.py','encodings/__init__.pyc','unrelated.pyc'):
            with self.subTest(entry=entry):
                with zipfile.ZipFile(package/'runtime/python314.zip','w') as archive:
                    archive.writestr(entry,b'synthetic layout fixture')
                for name in ('runtime/python.exe','runtime/python314.zip'):
                    fixture.value['files'][name]=build.sha(package/name)
                if entry.startswith('encodings/'):
                    build.validate(fixture.value)
                else:
                    with self.assertRaisesRegex(ValueError,'incomplete_python_stdlib'):
                        build.validate(fixture.value)


if __name__=='__main__':unittest.main()
