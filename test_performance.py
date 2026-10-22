import sys
sys.dont_write_bytecode=True
import threading, time, unittest, json
import hashlib, struct
from unittest.mock import patch
from PIL import Image
import engine

class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.folder=engine.SAVE/('performance-test-'+str(time.time_ns()))
        self.folder.mkdir()
        im=Image.new('RGBA',(64,64))
        for y in range(64):
            for x in range(64): im.putpixel((x,y),(x*4,y*4,((x//8+y//8)%2)*200,255 if x>15 else 0))
        im.save(self.folder/'a.png')
        im.resize((128,128),Image.Resampling.NEAREST).save(self.folder/'b.png')
    def scan(self,refresh=False):
        return engine.scan([str(self.folder)],dict(similar=False,resize=True,refresh=refresh),lambda *a:None,threading.Event(),self.folder/'index.sqlite')
    def test_pair_cache_refresh_and_content_change(self):
        original=engine.resize_match
        with patch('engine.resize_match',wraps=original) as verify:
            first=self.scan();self.assertEqual(verify.call_count,1)
            second=self.scan();self.assertEqual(verify.call_count,1)
            self.assertEqual(first['groups'],second['groups'])
            self.scan(True);self.assertEqual(verify.call_count,2)
            with Image.open(self.folder/'b.png') as source: changed=source.copy()
            r,g,b,a=changed.getpixel((60,60))
            changed.putpixel((60,60),(r+1,g,b,a));changed.save(self.folder/'b.png')
            self.scan();self.assertEqual(verify.call_count,3)
    def test_lru_eviction_and_raw_transparent_rgb(self):
        cache=engine.ResizePixels(limit=16*16*4*4)
        a=engine.read_image(str(self.folder/'a.png'))
        b=engine.read_image(str(self.folder/'b.png'))
        cache.get(a,(16,16));cache.get(b,(16,16))
        self.assertEqual(len(cache.items),1);self.assertLessEqual(cache.bytes,cache.limit)
        Image.new('RGBA',(32,32),(255,0,0,0)).save(self.folder/'a.png')
        Image.new('RGBA',(64,64),(0,255,0,0)).save(self.folder/'b.png')
        a=engine.read_image(str(self.folder/'a.png'));b=engine.read_image(str(self.folder/'b.png'))
        self.assertFalse(engine.resize_match(a,b,cache))

    def test_pixel_hash_and_grayscale_hash_keep_the_existing_values(self):
        with Image.open(self.folder/'a.png') as source: image=source.convert('RGBA')
        row=engine.read_image(str(self.folder/'a.png'))
        expected=hashlib.sha256(struct.pack('<II',*image.size)+image.tobytes()).hexdigest()
        self.assertEqual(row['pixelhash'],expected)
        self.assertEqual(row['rgbhash'],str(engine.phash(image.convert('RGB').convert('L'))))

    def test_thumbnail_cache_is_compact_and_reads_legacy_rows(self):
        row=engine.read_image(str(self.folder/'a.png'))
        self.assertIsInstance(row['thumb'],bytes)
        compact=engine.encode_image_cache(row)
        legacy=dict(row,thumb=row['thumb'].hex())
        legacy_json=json.dumps(legacy,ensure_ascii=False)
        self.assertLess(len(compact),len(legacy_json))
        expected=dict(row,alpha=tuple(row['alpha']))
        self.assertEqual(engine.decode_image_cache(compact),expected)
        self.assertEqual(engine.decode_image_cache(legacy_json),expected)

    def test_whole_result_reuse_and_invalidation(self):
        first=self.scan()
        self.assertFalse(first['timings']['结果复用'])
        self.assertIn('目录枚举',first['timings'])
        self.assertIn('缓存检查',first['timings'])
        self.assertIn('图像读取与特征',first['timings'])
        self.assertIn('总耗时',first['timings'])
        with patch('engine.score_contents',side_effect=AssertionError('unchanged library must reuse groups')):
            second=self.scan()
            self.assertEqual(first['groups'],second['groups'])
            self.assertTrue(second['timings']['结果复用'])
        original=engine.score_contents
        with patch('engine.score_contents',wraps=original) as score:
            Image.new('RGBA',(16,16),(8,9,10,255)).save(self.folder/'new.png')
            self.scan();self.assertEqual(score.call_count,1)
            # Remove a generated fixture, never a source texture.
            (self.folder/'new.png').unlink()
            self.scan();self.assertEqual(score.call_count,2)
            self.scan(True);self.assertEqual(score.call_count,3)
            engine.scan([str(self.folder)],dict(similar=True,resize=True),lambda *a:None,threading.Event(),self.folder/'index.sqlite')
            self.assertEqual(score.call_count,4)
        cancel=threading.Event();cancel.set()
        with self.assertRaises(engine.Cancelled):
            engine.scan([str(self.folder)],dict(similar=False,resize=True,refresh=False),lambda *a:None,cancel,self.folder/'index.sqlite')

if __name__=='__main__': unittest.main()
