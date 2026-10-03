"""Beta-only resource boundaries and real XML compilation; no APK/device work."""
from pathlib import Path
import hashlib
import math
import os
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / 'app/src/udp/res'
ANDROID = '{http://schemas.android.com/apk/res/android}'
SDK = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    or str(Path.home() / 'Library/Android/sdk'))
AAPT2 = SDK / 'build-tools/36.0.0/aapt2'


def outline_points(path):
    """Sample actual M/L/Q/Z badge edges, including their quadratic curves."""
    tokens = re.findall(r'[MLQZ]|-?\d+(?:\.\d+)?', path)
    index = 0
    current = start = None
    points = []
    while index < len(tokens):
        command = tokens[index]
        index += 1
        if command in ('M', 'L'):
            current = tuple(float(n) for n in tokens[index:index + 2])
            index += 2
            if command == 'M': start = current
            points.append(current)
        elif command == 'Q':
            control = tuple(float(n) for n in tokens[index:index + 2])
            end = tuple(float(n) for n in tokens[index + 2:index + 4])
            index += 4
            for step in range(1, 33):
                t = step / 32
                points.append(tuple((1 - t) ** 2 * current[d]
                    + 2 * (1 - t) * t * control[d] + t ** 2 * end[d]
                    for d in (0, 1)))
            current = end
        elif command == 'Z':
            current = start
            points.append(start)
        else:
            raise ValueError('unsupported badge path command')
    return points


class ExperimentalVisualIdentityChecks(unittest.TestCase):
    def test_formal_supplied_avatar_icon_palette_and_name_bytes_are_preserved(self):
        originals = {
            'app/src/main/res/drawable/avatar_photo.png': 'cd26b561944388d9cdc48d4931f035117ae9f69fe0b3dfdae324361ca73217a5',
            'app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml': 'f1c9031f018b90865f6a8c99868730402714e46a25cf30df0d009b91ae662378',
            'app/src/main/res/values/colors.xml': '0d07579888a7bb7e8180f83d0aa9fa1e11abdc8abd44edca5dec20459a656be8',
            'app/src/main/res/values/strings.xml': '77cac4050dc33a6c2ce93fb16abc20d2fc1a540c8b17bd4169a59057a1fbb355',
        }
        for name, digest in originals.items():
            with self.subTest(resource=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)

    def test_manifest_and_conditional_sources_choose_beta_only_for_udp_variant(self):
        application = ET.parse(ROOT / 'app/src/main/AndroidManifest.xml').getroot().find('application')
        self.assertEqual(application.attrib[ANDROID + 'icon'], '${appIcon}')
        self.assertEqual(application.attrib[ANDROID + 'roundIcon'], '${appIcon}')
        self.assertEqual(application.attrib[ANDROID + 'theme'], '${appTheme}')
        gradle = (ROOT / 'app/build.gradle').read_text()
        self.assertRegex(gradle, r"appIcon:\s*authenticatedLanUdp\s*\?\s*'@mipmap/ic_launcher_experimental'\s*:\s*'@mipmap/ic_launcher'")
        self.assertRegex(gradle, r"appTheme:\s*authenticatedLanUdp\s*\?\s*'@style/HuoguoExperimentalTheme'\s*:\s*'@android:style/Theme.Material.Light.NoActionBar'")
        conditional = gradle[gradle.index('if (authenticatedLanUdp) {\n    def generatedJava'):]
        self.assertIn("android.sourceSets.main.res.srcDirs += ['src/udp/res']", conditional)
        self.assertEqual(gradle.count("res.srcDirs += ['src/udp/res']"), 1)
        self.assertIn("appLabel: authenticatedLanUdp ? '给火锅的安卓 · 实验版' : '@string/app_name'", gradle)

    def test_adaptive_beta_icon_reuses_avatar_and_overlays_badge(self):
        icon = ET.parse(RES / 'mipmap-anydpi-v26/ic_launcher_experimental.xml').getroot()
        self.assertEqual(icon.tag, 'adaptive-icon')
        self.assertEqual(icon.find('foreground').attrib[ANDROID + 'drawable'], '@drawable/ic_launcher_experimental_foreground')
        self.assertEqual(icon.find('monochrome').attrib[ANDROID + 'drawable'], '@drawable/ic_launcher_experimental_monochrome')
        foreground = ET.parse(RES / 'drawable/ic_launcher_experimental_foreground.xml').getroot()
        self.assertEqual(foreground.tag, 'layer-list')
        self.assertEqual(len(foreground.findall('item')), 2)
        inset = foreground.find('item/inset')
        self.assertEqual(inset.attrib[ANDROID + 'inset'], '10dp')
        self.assertEqual(inset.find('bitmap').attrib[ANDROID + 'src'], '@drawable/avatar_photo')
        self.assertEqual(foreground.findall('item')[1].attrib[ANDROID + 'drawable'], '@drawable/ic_experimental_test_badge')

    def test_badge_edge_and_stroke_fit_circular_adaptive_icon_safe_zone(self):
        vector = ET.parse(RES / 'drawable/ic_experimental_test_badge.xml').getroot()
        self.assertEqual((vector.attrib[ANDROID + 'viewportWidth'], vector.attrib[ANDROID + 'viewportHeight']), ('108', '108'))
        background = vector.find('path')
        radius = float(background.attrib[ANDROID + 'strokeWidth']) / 2
        for x, y in outline_points(background.attrib[ANDROID + 'pathData']):
            self.assertLessEqual(math.hypot(x - 54, y - 54) + radius, 36)
        color_glyph = vector.find('group/path')
        monochrome = ET.parse(RES / 'drawable/ic_launcher_experimental_monochrome.xml').getroot()
        self.assertEqual(color_glyph.attrib[ANDROID + 'pathData'], monochrome.find('group/path').attrib[ANDROID + 'pathData'])
        self.assertEqual(color_glyph.attrib[ANDROID + 'strokeLineCap'], 'round')

    def test_beta_theme_activates_accent_and_white_badge_contrast(self):
        resources = ET.parse(RES / 'values/experimental_theme.xml').getroot()
        colors = {item.attrib['name']: item.text for item in resources.findall('color')}
        theme = resources.find('style')
        self.assertEqual(theme.attrib['parent'], '@android:style/Theme.Material.Light.NoActionBar')
        items = {item.attrib['name']: item.text for item in theme.findall('item')}
        self.assertEqual(items['android:colorAccent'], '@color/experimental_accent')
        self.assertEqual(items['android:colorControlActivated'], items['android:colorAccent'])
        self.assertEqual(items['android:windowLightStatusBar'], 'true')
        rgb = [int(colors['experimental_accent'][i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        luminance = sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
        self.assertGreaterEqual(1.05 / (luminance + .05), 4.5)

    def test_actual_beta_resource_xml_compiles_without_building_an_apk(self):
        if not AAPT2.is_file(): raise RuntimeError('Existing Android aapt2 required')
        with tempfile.TemporaryDirectory(prefix='huoguo-beta-resource-') as folder:
            output = Path(folder) / 'compiled-resources.zip'
            result = subprocess.run([str(AAPT2), 'compile', '--dir', str(RES), '-o', str(output)],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(output.is_file())


if __name__ == '__main__': unittest.main()
