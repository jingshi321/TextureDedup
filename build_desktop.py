"""Reproducible one-file build; all generated build state stays in save/."""
import sys,os
sys.dont_write_bytecode=True
from pathlib import Path
root=Path(__file__).resolve().parent
save=root/'save'
for name in ['tmp','desktop-build','desktop-spec','pyinstaller-cache']:(save/name).mkdir(exist_ok=True)
os.environ['TEMP']=os.environ['TMP']=str(save/'tmp')
os.environ['PYINSTALLER_CONFIG_DIR']=str(save/'pyinstaller-cache')
sys.path[:0]=[str(save/'gpu-deps'),str(save/'desktop-deps'),str(save/'deps')]
import PyInstaller.__main__
PyInstaller.__main__.run([
    str(root/'desktop.py'),'--name',sys.argv[1] if len(sys.argv)>1 else '纹理镜_BY月华_高速适配版','--onefile','--windowed','--noupx',
    '--distpath',str(root/'Windows版'),'--workpath',str(save/'desktop-build'),
    '--specpath',str(save/'desktop-spec'),
    '--paths',str(save/'gpu-deps'),'--paths',str(save/'desktop-deps'),'--paths',str(save/'deps'),
    '--add-data',str(root/'index.html')+os.pathsep+'.',
    '--add-data',str(root/'app.js')+os.pathsep+'.',
    '--add-data',str(save/'models'/'texture-features-batch.onnx')+os.pathsep+'models',
    '--add-data',str(save/'models'/'ONNX-MODELS-LICENSE')+os.pathsep+'models',
    '--add-data',str(save/'models'/'source.json')+os.pathsep+'models',
    '--collect-all','webview','--collect-all','clr_loader','--collect-all','pythonnet',
    '--collect-all','onnxruntime','--collect-submodules','send2trash',
    '--exclude-module','onnx','--exclude-module','torch','--exclude-module','tensorflow',
    '--exclude-module','matplotlib','--exclude-module','pandas','--exclude-module','scipy',
    '--exclude-module','PyQt5','--exclude-module','PyQt6','--exclude-module','PySide2',
    '--exclude-module','PySide6','--exclude-module','pytest','--exclude-module','IPython',
])
