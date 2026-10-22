import unittest
from vfx_filter import classify_texture, texture_keywords


class VfxFilterTests(unittest.TestCase):
    def test_icon_exclusions_override_effect_words(self):
        for path in ['art/2dart/skillicons/lightning.png','art/2ditems/effects/fire.png',
                     'VFX/UI/lightning.png','art/textures/interface/fire.png',
                     'effects/skill_icons/flame.png','effects/T_Fire_Icon.png']:
            self.assertEqual(classify_texture(path)[0],'excluded',path)

    def test_effect_assets_and_uncertain_assets(self):
        for path in ['art/particles/monster_particles/fire.png',
                     'art/textures/misc/Noise_01.png','T_Flowmap_02.tga']:
            self.assertEqual(classify_texture(path)[0],'selected',path)
        self.assertEqual(classify_texture('art/particles/fxlib/pbr/map.png')[0],'excluded')
        self.assertEqual(classify_texture('烟雾_01.png')[0],'unknown')
        self.assertEqual(classify_texture('art/textures/items/armour/fire.png')[0],'excluded')
        self.assertEqual(classify_texture('textures/misc/unknown.png')[0],'unknown')

    def test_material_maps_and_ambiguous_asset_names_are_not_selected(self):
        for path in ['effects/beam_orb_color.png','effects/beam_orb_glow.png',
                     'effects/beam_orb_mask.png','effects/beam_orb_meta.png',
                     'effects/beam_orb_normal.png','textures/barbed_fire_col.png',
                     'textures/barbed_lightning.png','art/pbr/smoke.png']:
            self.assertNotEqual(classify_texture(path)[0],'selected',path)
        self.assertEqual(classify_texture('effects/dissolve_noise_01.png')[0],'selected')

    def test_keyword_tags_include_compound_names_and_directory_names(self):
        self.assertEqual(texture_keywords('fire/noise_01.png'),['fire','noise'])
        self.assertIn('fire',texture_keywords('misc/fireball.png'))


if __name__=='__main__': unittest.main()
