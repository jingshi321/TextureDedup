from pathlib import Path
import subprocess,sys,urllib.request,hashlib,json,os
root=Path(__file__).resolve().parent
save=root/'save';save.mkdir(exist_ok=True)
os.environ['PIP_CACHE_DIR']=str(save/'pip-cache')
os.environ['TMP']=os.environ['TEMP']=str(save/'tmp')
(save/'tmp').mkdir(exist_ok=True)
sys.dont_write_bytecode=True
deps=save/'deps';deps.mkdir(exist_ok=True)
subprocess.run([sys.executable,'-m','pip','install','--target',str(deps),'onnxruntime','onnx','--disable-pip-version-check'],check=True)
models=save/'models';models.mkdir(exist_ok=True)
url='https://media.githubusercontent.com/media/onnx/models/main/validated/vision/classification/mobilenet/model/mobilenetv2-7.onnx'
target=models/'mobilenetv2-7.onnx'
if not target.exists():urllib.request.urlretrieve(url,target)
sys.path.insert(0,str(deps));import onnx
model=onnx.load(str(target))
nodes=list(model.graph.node)
print('TAIL',[(n.op_type,list(n.input),list(n.output)) for n in nodes[-8:]])
# Expose pooled convolutional features rather than classification probabilities.
pool=next(n for n in reversed(nodes) if n.op_type=='GlobalAveragePool')
del model.graph.output[:]
model.graph.output.append(onnx.helper.make_tensor_value_info(pool.output[0],onnx.TensorProto.FLOAT,[1,1280,1,1]))
onnx.checker.check_model(model)
onnx.save(model,str(models/'texture-features.onnx'))
(models/'source.json').write_text(json.dumps({'url':url,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'features':'GlobalAveragePool MobileNetV2 ImageNet'}),encoding='utf-8')
print('AI READY')
