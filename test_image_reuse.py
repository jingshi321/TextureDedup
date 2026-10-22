import sys
sys.dont_write_bytecode=True
import unittest,time,threading,sqlite3
from unittest.mock import patch
import engine as e


class ImageReuseTests(unittest.TestCase):
    def setUp(self):
        self.folder=e.SAVE/('reuse-test-'+str(time.time_ns()));self.folder.mkdir()
        self.store=e.PreparedImages(self.folder/'prepared.sqlite')
        self.addCleanup(self.store.close)
        rng=e.np.random.default_rng(11)
        self.rgba=rng.integers(0,256,(270,380,4),dtype=e.np.uint8)
        self.path=self.folder/'image.png';e.Image.fromarray(self.rgba).save(self.path)

    def test_lossless_raw_channels_and_ai_without_reopening_source(self):
        row=e.read_image(str(self.path),self.store,True)
        reference=e.ResizePixels().get(row,(256,256))
        expected=e.FeatureModel.prepare(str(self.path))
        # Comparison thumbnails are prepared only when a candidate needs them.
        e.ResizePixels(prepared=self.store).get(row,(256,256))
        with patch.object(e.Image,'open',side_effect=AssertionError('source decoded again')):
            actual=e.ResizePixels(prepared=self.store).get(row,(256,256))
            e.np.testing.assert_array_equal(actual,reference)
            e.np.testing.assert_array_equal(e.ai_array(self.store.get(row,'ai')),expected)
        # A smaller common comparison size must resize the original, not a thumbnail.
        e.np.testing.assert_array_equal(e.ResizePixels(prepared=self.store).get(row,(63,97)),e.ResizePixels().get(row,(63,97)))

    def test_rgba_source_skips_redundant_full_image_conversion(self):
        original=e.Image.Image.convert
        def convert(image,mode,*args,**kwargs):
            if image.mode=='RGBA' and mode=='RGBA':raise AssertionError('RGBA image should be reused')
            return original(image,mode,*args,**kwargs)
        with patch.object(e.Image.Image,'convert',new=convert):
            row=e.read_image(str(self.path))
        self.assertEqual(row['pixelhash'],e.hashlib.sha256(__import__('struct').pack('<II',*self.rgba.shape[1::-1])+self.rgba.tobytes()).hexdigest())

    def test_disk_limit_and_missing_data_fallback(self):
        tiny=e.PreparedImages(self.folder/'tiny.sqlite',limit=10)
        try:
            row=e.read_image(str(self.path),tiny,True)
            self.assertLessEqual(tiny.bytes,10)
            self.assertIsNone(tiny.get(row,'box'))
            e.np.testing.assert_array_equal(e.ResizePixels(prepared=tiny).get(row,(256,256)),e.ResizePixels().get(row,(256,256)))
        finally:tiny.close()

    def test_parallel_requests_decode_same_image_once(self):
        row=e.read_image(str(self.path));cache=e.ResizePixels()
        with patch.object(e.Image,'open',wraps=e.Image.open) as opened:
            with e.ThreadPoolExecutor(max_workers=6) as pool:
                values=list(pool.map(lambda _:cache.get(row,(256,256)),range(12)))
            self.assertEqual(opened.call_count,1)
            self.assertTrue(all(value is values[0] for value in values))

    def test_identical_files_reuse_decoded_features(self):
        duplicate=self.folder/'duplicate.png';duplicate.write_bytes(self.path.read_bytes())
        reuse=e.DecodedFileReuse()
        original=e.Image.open
        def slow_open(*args,**kwargs):
            time.sleep(.03)
            return original(*args,**kwargs)
        with patch.object(e.Image,'open',side_effect=slow_open) as opened:
            with e.ThreadPoolExecutor(max_workers=2) as pool:
                first,second=pool.map(lambda path:e.read_image(path,file_reuse=reuse),(str(self.path),str(duplicate)))
        self.assertEqual(opened.call_count,1)
        self.assertEqual(first['filehash'],second['filehash'])
        self.assertEqual(first['pixelhash'],second['pixelhash'])
        self.assertEqual(first['rgbhash'],second['rgbhash'])
        self.assertEqual(first['alphahash'],second['alphahash'])
        self.assertEqual(first['path'],str(self.path))
        self.assertEqual(second['path'],str(duplicate))
        self.assertEqual(reuse.hits,1)

    def test_scan_reports_decoded_file_reuse(self):
        duplicate=self.folder/'scan_duplicate.png';duplicate.write_bytes(self.path.read_bytes())
        result=e.scan([str(self.folder)],{'similar':False,'resize':False,'alpha':True,'ai':False},lambda *args:None,threading.Event(),self.folder/'scan.sqlite')
        self.assertEqual(result['timings']['解码复用'],1)

    def test_duplicate_score_pairs_share_work(self):
        a=e.read_image(str(self.path),self.store)
        path=self.folder/'other.png';e.Image.fromarray(self.rgba^1).save(path)
        b=e.read_image(str(path),self.store)
        result=dict(records=[a,b],options={'alpha':True},errors=[],groups=[dict(id=i,ids=[0,1],kind='外观相似',score=1.) for i in range(8)])
        with sqlite3.connect(':memory:') as db:
            with patch.object(e,'content_similarity',wraps=e.content_similarity) as metric:
                e.score_contents(result,db,lambda *a:None,threading.Event(),e.ResizePixels(prepared=self.store))
                self.assertEqual(metric.call_count,1)
        self.assertEqual(len({g['score'] for g in result['groups']}),1)

    def test_batched_candidates_and_threshold_boundaries(self):
        rng=e.np.random.default_rng(21)
        matrix=rng.normal(size=(600,1280)).astype(e.np.float32)
        matrix/=e.np.linalg.norm(matrix,axis=1,keepdims=True)
        batch=list(range(512));expected=matrix[batch]@matrix[599]
        for threshold in [.97,float(expected[90]),float(expected[30])+1e-8]:
            actual=e.ai_candidate_scores(matrix,batch,599,threshold)
            e.np.testing.assert_allclose(actual,expected,atol=2e-7)
            e.np.testing.assert_array_equal(actual>=threshold,expected>=threshold)


if __name__=='__main__':unittest.main()
