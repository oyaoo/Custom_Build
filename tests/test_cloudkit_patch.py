"""执行工作流中的实际补丁，验证完整替换 CloudKit 调用且保留相邻代码。"""
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/build-ollama-timeout.yml').read_text()
STEP = WORKFLOW.split('      - name: Disable CloudKit for local build\n', 1)[1].split(
    '      - name: Select Xcode and resolve Swift packages\n', 1
)[0]
PATCH = textwrap.dedent(re.search(
    r"(?ms)^          python3 <<'PY'\n(.*?)^          PY\n", STEP
).group(1))

CALL = '''SyncEngine.isCloudSyncSupported(
            containerIdentifier: CloudKitConfig.containerIdentifier
        )'''
SOURCE = '''func live() {
        let shouldEnableCloudSync = CALL
        let shouldUseMockSync = isRunningTests || !shouldEnableCloudSync
        let syncEngine = SyncEngine(
            storage: storage,
            containerIdentifier: CloudKitConfig.containerIdentifier,
            mode: .mock
        )
}
'''
REPLACEMENT = 'false // local ad-hoc build'


class CloudKitPatchTests(unittest.TestCase):
    def patch(self, text):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            path = root / 'FlowDown/Application/AppEnvironment.swift'
            path.parent.mkdir(parents=True)
            if text is not None:
                path.write_text(text)
            result = subprocess.run([sys.executable, '-c', PATCH], cwd=root,
                                    capture_output=True, text=True)
            return result, path.read_text() if path.exists() else None

    def test_multiline_call_replaced_without_changing_adjacent_code(self):
        result, actual = self.patch(SOURCE.replace('CALL', CALL))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(actual, SOURCE.replace('CALL', REPLACEMENT))

    def test_single_line_call(self):
        call = 'SyncEngine.isCloudSyncSupported(containerIdentifier: CloudKitConfig.containerIdentifier)'
        result, actual = self.patch(SOURCE.replace('CALL', call))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(actual, SOURCE.replace('CALL', REPLACEMENT))

    def test_trailing_comma_and_crlf(self):
        call = CALL.replace('CloudKitConfig.containerIdentifier', 'CloudKitConfig.containerIdentifier,')
        result, actual = self.patch(SOURCE.replace('CALL', call).replace('\n', '\r\n'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(actual, SOURCE.replace('CALL', REPLACEMENT))

    def test_changed_argument_rejected_without_writing(self):
        source = SOURCE.replace('CALL', CALL.replace('CloudKitConfig.containerIdentifier', 'anotherContainer'))
        result, actual = self.patch(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('upstream CloudKit setting changed', result.stderr)
        self.assertEqual(actual, source)

    def test_duplicate_target_rejected_without_writing(self):
        source = SOURCE.replace('CALL', CALL) * 2
        result, actual = self.patch(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(actual, source)

    def test_missing_file_rejected(self):
        result, actual = self.patch(None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('file not found', result.stderr)
        self.assertIsNone(actual)

    def test_reported_bug_reproduced_and_removed(self):
        source = SOURCE.replace('CALL', CALL)
        old = source.replace('let shouldEnableCloudSync = SyncEngine.isCloudSyncSupported',
                             'let shouldEnableCloudSync = ' + REPLACEMENT)
        self.assertIn('false // local ad-hoc build(\n            containerIdentifier:', old)
        result, actual = self.patch(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('false // local ad-hoc build(', actual)
        self.assertEqual(actual.count('containerIdentifier:'), 1)  # 保留 SyncEngine 构造参数。

    def test_swift_parse_happens_before_dependency_resolution(self):
        selection = WORKFLOW.split('      - name: Select Xcode and resolve Swift packages\n', 1)[1]
        selection = selection.split('      - name: Archive macOS app\n', 1)[0]
        self.assertLess(selection.index('xcodebuild -version'), selection.index('xcrun swiftc -frontend -parse'))
        self.assertLess(selection.index('xcrun swiftc -frontend -parse'), selection.index('-downloadComponent'))
        self.assertLess(selection.index('xcrun swiftc -frontend -parse'), selection.index('-resolvePackageDependencies'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
