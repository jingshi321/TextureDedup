import sys
sys.dont_write_bytecode=True
import unittest, json, threading, urllib.request, urllib.error
import server

class SimilarityTests(unittest.TestCase):
    def fixture(self):
        row=dict(path='example.png',size=100,w=16,h=16,alpha=[0,255])
        groups=[dict(id=i,ids=[0,1],kind='外观相似',score=score) for i,score in enumerate([.989999,.99,.995,1.0])]
        groups.append(dict(id=4,ids=[0,1],kind='文件完全重复',score=1.0))
        return dict(groups=groups,records=[row,row],errors=[])
    def test_exact_boundary_and_kind(self):
        r=self.fixture()
        self.assertEqual([g['id'] for g in server.filter_groups(r,{'min_similarity':['99']})],[1,2,3,4])
        self.assertEqual([g['id'] for g in server.filter_groups(r,{'min_similarity':['99.5'],'kind':['外观相似']})],[2,3])
        self.assertEqual(len(server.filter_groups(r,{})),5)
    def test_invalid_values(self):
        for value in ['nan','inf','-1','101','bad']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.filter_groups(self.fixture(),{'min_similarity':[value]})
    def test_all_types_mixed_pagination(self):
        kinds=['文件完全重复','RGBA 像素重复','等比例缩放候选','非等比例拉伸候选','外观相似','AI 近似']
        result=self.fixture()
        result['records'][1]=dict(result['records'][1],size=200)
        result['groups']=[dict(id=k*15+n,ids=[0,1],kind=kind,score=1 if n<14 else .9) for k,kind in enumerate(kinds) for n in range(15)]
        original=server.STATE['result'];server.STATE['result']=result
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        def get(query):
            with urllib.request.urlopen('http://127.0.0.1:'+str(http.server_port)+'/api/results?'+query) as response:return json.load(response)
        try:
            first=get('min_similarity=99')
            self.assertEqual(set(g['kind'] for g in first['groups']),set(kinds))
            self.assertEqual(first['counts'],dict.fromkeys(kinds,14))
            self.assertEqual(first['total'],84)
            with urllib.request.urlopen('http://127.0.0.1:'+str(http.server_port)+'/api/select-largest?min_similarity=99') as response:selected=json.load(response)
            self.assertEqual(selected['total'],84)
            self.assertTrue(all(s['keep']==1 for s in selected['selections']))
            self.assertTrue(all(s['kind']==result['groups'][s['group']]['kind'] and s['score']==result['groups'][s['group']]['score'] for s in selected['selections']))
            self.assertEqual({s['group'] for s in selected['selections']},{g['id'] for g in server.filter_groups(result,{'min_similarity':['99']})})
            ids=[]
            for offset in range(0,84,12): ids.extend(g['id'] for g in get('min_similarity=99&offset='+str(offset))['groups'])
            self.assertEqual(len(ids),len(set(ids)))
            self.assertEqual(set(ids),{g['id'] for g in server.filter_groups(result,{'min_similarity':['99']})})
            for kind in kinds:
                data=get('min_similarity=99&kind='+urllib.parse.quote(kind))
                self.assertEqual(data['total'],14)
                self.assertTrue(all(g['kind']==kind for g in data['groups']))
                with urllib.request.urlopen('http://127.0.0.1:'+str(http.server_port)+'/api/select-largest?min_similarity=99&kind='+urllib.parse.quote(kind)) as response:chosen=json.load(response)
                self.assertEqual(chosen['total'],14)
                self.assertTrue(all(result['groups'][s['group']]['kind']==kind for s in chosen['selections']))
        finally:
            http.shutdown();http.server_close();server.STATE['result']=original
    def test_results_filter_and_removed_export_endpoints(self):
        original=server.STATE['result'];server.STATE['result']=self.fixture()
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        def get(path):
            with urllib.request.urlopen('http://127.0.0.1:'+str(http.server_port)+path) as response:return response.read()
        try:
            query='min_similarity=99.5&kind='+urllib.parse.quote('外观相似')
            data=json.loads(get('/api/results?'+query+'&offset=1'))
            self.assertEqual(data['total'],2);self.assertEqual([g['id'] for g in data['groups']],[3])
            for result in (self.fixture(),None):
                server.STATE['result']=result
                for format in ('csv','json'):
                    with self.assertRaises(urllib.error.HTTPError) as removed:
                        get('/api/export?format='+format+'&'+query)
                    self.assertEqual(removed.exception.code,404)
            server.STATE['result']=self.fixture()
            with self.assertRaises(urllib.error.HTTPError): get('/api/results?min_similarity=nan')
        finally:
            http.shutdown();http.server_close();server.STATE['result']=original

if __name__=='__main__':unittest.main(verbosity=2)
