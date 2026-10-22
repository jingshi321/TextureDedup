"""Conservative VFX asset selection from resource paths, not visual subject names."""
import re

KEYWORDS=('beam','blood','bubble','burn','dash','debris','distortion','dissolve','dust','electric',
         'energy','explosion','fire','fireball','flame','flipbook','flowmap','fog','frost','glow',
         'ice','impact','laser','lightning','magic','muzzle','noise','particle','portal','projectile',
         'ripple','ring','slash','smoke','snow','spark','spell','spritesheet','steam','shockwave',
         'splash','teleport','trail','warp','water','wave','wind',
         '火焰','火球','烟雾','噪声','闪电','光束','粒子','爆炸','冲击波','水花','冰霜','溶解','扰动','序列帧')


def texture_keywords(path):
    text=str(path).casefold()
    words=re.findall(r'[a-z]+',text)
    return sorted(k for k in KEYWORDS if (k in text if not k.isascii() else any(w==k or w.startswith(k) for w in words)))


def classify_texture(path):
    parts=[p.casefold() for p in re.split(r'[\\/]+',str(path)) if p]
    words=[set(re.findall(r'[a-z]+|[\u4e00-\u9fff]+',p)) for p in parts]
    directories=parts[:-1]
    # UI pictures can depict fire/lightning too: exclusions always win.
    excluded={'2dart','2ditems','ui','gui','interface','interfaces','hud','icons','icon',
              'skillicons','skill_icons','inventory','portraits','portrait','avatars',
              'minimap','fonts','font','loading','loadingscreens','screenshots',
              '技能图标','图标','界面','头像'}
    if any(p in excluded or p.endswith('icons') or p.endswith('_icons') for p in directories):
        return 'excluded','排除图标 / 界面资源目录'
    name=parts[-1] if parts else ''
    name_words=words[-1] if words else set()
    if name_words & {'icon','icons','portrait','avatar','hud','ui','logo'} or re.match(r'^(?:skillicon|itemicon|inventoryicon)',name):
        return 'excluded','排除图标 / 界面文件名'
    stem=re.sub(r'\.[^.]+$','',name)
    file_words=re.findall(r'[a-z]+|[\u4e00-\u9fff]+',re.sub(r'[_-]?\d+$','',stem))
    material_channels={'col','color','colour','albedo','diff','diffuse','basecolor','basecolour',
                       'nor','normal','nrm','rough','roughness','metal','metallic','meta','orm',
                       'mra','ao','height','displacement','opacity','mask','glow'}
    if file_words and file_words[-1] in material_channels:
        return 'excluded','疑似材质通道贴图'
    material_dirs={'material','materials','pbr','pbrtextures','材质'}
    if any(p in material_dirs for p in directories):
        return 'excluded','材质 / PBR 资源目录'
    effect_dirs={'particles','particle','vfx','fx','effects','effect','fxlib','flipbooks',
                 'flipbook','spritesheets','noise','noises','masks','dissolve',
                 '特效','粒子','噪声','遮罩','序列帧'}
    if any(p in effect_dirs or w & {'particles','particle','vfx','fx','effects'} for p,w in zip(directories,words)):
        return 'selected','特效 / 粒子素材目录'
    ordinary={'items','weapons','weapon','armour','armours','armor','armors','characters',
              'charactersfour','character','monsters','terrain','environment','bodyarmours'}
    if any(p in ordinary for p in directories):
        return 'excluded','排除角色 / 装备 / 场景贴图目录'
    terms={'particle','particles','vfx','fx','flipbook','spritesheet','noise','dissolve',
           'distortion','flowmap','shockwave','ripple','特效','噪声','溶解'}
    if name_words & terms or re.match(r'^(?:smoke|noise|flipbook|flowmap|dissolve|distortion|shockwave|spark|flame|gradient)\d',name):
        return 'selected','特效素材文件名'
    return 'unknown','用途不明确，暂不选择'
