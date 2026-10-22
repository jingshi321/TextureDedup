import sys
sys.dont_write_bytecode=True
import tempfile,unittest,time
from pathlib import Path
from unittest.mock import patch
from desktop import cleanup_cache,window_geometry


class CacheTests(unittest.TestCase):
    def test_window_uses_target_size_when_available_and_fits_regular_display(self):
        self.assertEqual(window_geometry((0,0,3840,2160)),(491,255,2858,1650))
        self.assertEqual(window_geometry((0,0,1920,1040)),(40,40,1840,960))
        self.assertEqual(window_geometry((0,0,1366,728)),(40,40,1286,648))
        self.assertEqual(window_geometry((0,0,1280,680)),(40,40,1200,600))
        self.assertEqual(window_geometry((0,0,1024,700)),(40,40,944,620))
        self.assertEqual(window_geometry((0,0,500,400)),(10,0,480,400))

    def test_dpi_changes_units_without_enlarging_the_physical_window(self):
        for area in [(0,0,3840,2088),(0,0,1920,1040),(-1920,0,0,1040)]:
            expected=window_geometry(area)
            for scale in [1.0,1.25,1.5,1.75,2.0,2.5,3.0]:
                with self.subTest(area=area,scale=scale):
                    # WinForms uses int(logical * DPI scale) for its outer rect.
                    logical=window_geometry(area,scale)
                    self.assertEqual(tuple(int(value*scale) for value in logical),expected)
        self.assertEqual(window_geometry((0,0,3840,2088),0),window_geometry((0,0,3840,2088)))

    def setUp(self):
        self.root=Path(__file__).resolve().parent/'save'/('desktop-test-'+str(time.time_ns()))
        self.root.mkdir()

    def test_only_owned_temp_child_is_removed(self):
        own=self.root/'TextureDedup-own';own.mkdir();(own/'.owned-cache').write_text('secret',encoding='ascii')
        (own/'webview').mkdir();(own/'webview'/'cache').write_bytes(b'generated')
        other=self.root/'TextureDedup-other';other.mkdir();(other/'important.txt').write_text('keep')
        with patch.object(tempfile,'gettempdir',return_value=str(self.root)):
            self.assertFalse(cleanup_cache(own,'wrong'))
            self.assertFalse(cleanup_cache(other,'secret'))
            self.assertTrue(cleanup_cache(own,'secret'))
        self.assertFalse(own.exists());self.assertTrue((other/'important.txt').exists())

    def test_correct_marker_outside_temp_is_rejected(self):
        (self.root/'.owned-cache').write_text('secret',encoding='ascii')
        self.assertFalse(cleanup_cache(self.root,'secret'))
        self.assertTrue(self.root.exists())


if __name__=='__main__':unittest.main()
