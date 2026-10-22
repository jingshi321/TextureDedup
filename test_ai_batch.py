import sys
sys.dont_write_bytecode=True
import unittest,sqlite3,time
from pathlib import Path
from unittest.mock import patch
import engine as e


class BatchFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cpu=e.FeatureModel(prefer_gpu=False);cls.fast=e.FeatureModel()
        rng=e.np.random.default_rng(4)
        cls.arrays=[rng.normal(size=(3,224,224)).astype(e.np.float32) for _ in range(3)]

    def test_batch_and_partial_gpu_match_cpu(self):
        reference=self.cpu.encode_batch(self.arrays)
        actual=self.fast.encode_batch(self.arrays)
        e.np.testing.assert_allclose(e.np.sum(actual*reference,axis=1),1,atol=1e-5)
        e.np.testing.assert_allclose(e.np.linalg.norm(actual,axis=1),1,atol=1e-6)

    def test_gpu_execution_error_falls_back_to_cpu(self):
        if not self.fast.gpu:self.skipTest('GPU unavailable')
        real=self.fast.session
        try:
            with patch.object(self.fast,'session') as session:
                session.run.side_effect=RuntimeError('simulated GPU failure')
                actual=self.fast.encode_batch(self.arrays)
            self.assertFalse(self.fast.gpu)
            self.assertIn('simulated',self.fast.fallback)
            e.np.testing.assert_allclose(actual,self.cpu.encode_batch(self.arrays),atol=1e-6)
        finally:self.fast.session=real;self.fast.gpu=True;self.fast.batch_size=16;self.fast.provider='GPU · DirectML'


class ExtractionTests(unittest.TestCase):
    def test_scope_keeps_effect_representative_and_exact_scan(self):
        folder=e.SAVE/('ai-scope-'+str(time.time_ns()));(folder/'skillicons').mkdir(parents=True);(folder/'particles').mkdir()
        e.Image.new('RGBA',(32,32),(10,20,30,255)).save(folder/'skillicons'/'fire.png')
        e.Image.new('RGBA',(32,32),(10,20,30,255)).save(folder/'particles'/'smoke.png')
        e.Image.new('RGBA',(32,32),(200,20,30,255)).save(folder/'skillicons'/'lightning.png')
        records=[e.read_image(str(p)) for p in [folder/'skillicons'/'fire.png',folder/'particles'/'smoke.png',folder/'skillicons'/'lightning.png']]
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.execute('CREATE TABLE features(hash TEXT PRIMARY KEY,vector BLOB)')
        with patch.object(e,'FeatureModel') as cls:
            model=cls.return_value;model.batch_size=16;model.provider='test';model.fallback=None
            model.prepare.side_effect=lambda path:e.np.zeros((3,224,224),e.np.float32)
            model.encode_batch.side_effect=lambda arrays:e.np.ones((len(arrays),1280),e.np.float32)
            ids,vectors,errors,stats=e.extract_ai(records,[0,2],db,{'ai_vfx_only':True},lambda *a:None,lambda:None)
            self.assertEqual(ids,[1]);self.assertEqual(stats['excluded'],1);self.assertEqual(errors,[])
            e.extract_ai(records,[0,2],db,{'ai_vfx_only':True},lambda *a:None,lambda:None)
            self.assertEqual(model.encode_batch.call_count,1)
        result=e.scan([str(folder)],{'ai':False,'similar':False,'resize':False},lambda *a:None,e.threading.Event(),folder/'index.sqlite')
        self.assertEqual(len(result['records']),3)
        self.assertTrue(any(g['kind']=='文件完全重复' for g in result['groups']))


if __name__=='__main__':unittest.main()
