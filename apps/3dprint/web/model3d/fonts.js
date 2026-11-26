// Stable keys are saved in 3D projects. All fonts are original local static TrueType files.
export const DEFAULT_FONT = 'mkv-sans';
export const TEXT_FONTS = [
  {"key": "mkv-sans", "name": "Metrophobic", "style": "Enkel", "file": "Metrophobic-Regular.ttf", "sha256": "34e4ac3257307c28e6dd14f5d77bb296211f0ddd217607228a5375416ed8ac58"},
  {"key": "archivo-black", "name": "Archivo Black", "style": "Ekstra fed", "file": "ArchivoBlack-Regular.ttf", "sha256": "dd9a89a019b4849f66ab75455fe7bdf931311042cbb0f0f97acc061539703180"},
  {"key": "titan-one", "name": "Titan One", "style": "Rund og tyk", "file": "TitanOne-Regular.ttf", "sha256": "563ff6de179bbd44bcd7d2a6c448d6dc3bf935834237cf1250e506e65cd86ff1"},
  {"key": "anton", "name": "Anton", "style": "Høj og smal", "file": "Anton-Regular.ttf", "sha256": "a4ba3a92350ebb031da0cb47630ac49eb265082ca1bc0450442f4a83ab947cab"},
  {"key": "alfa-slab-one", "name": "Alfa Slab One", "style": "Kraftige fødder", "file": "AlfaSlabOne-Regular.ttf", "sha256": "28664afa698a3393bd5a29eec750230a0645c5301d62200e5f2d3a027fb2299d"},
  {"key": "bungee", "name": "Bungee", "style": "Arkadeskrift", "file": "Bungee-Regular.ttf", "sha256": "c4f5361ce120af3e6b9156d0bf379fa19cda2ea0cd18ac01fd99596c6bf66e3f"},
  {"key": "righteous", "name": "Righteous", "style": "Rund og geometrisk", "file": "Righteous-Regular.ttf", "sha256": "2ffb3fe5c27d7e6571210b800448c4e234e651b46c6b4426c1bb567e5341348a"},
  {"key": "bree-serif", "name": "Bree Serif", "style": "Bløde fødder", "file": "BreeSerif-Regular.ttf", "sha256": "fd080582d70112ef479ba4ddd72407f9a4fcbcec4caa7d29de259b13eeb5f431"},
  {"key": "kalam-bold", "name": "Kalam Bold", "style": "Håndskrift", "file": "Kalam-Bold.ttf", "sha256": "2f6576601db015d4f6c08678120277fc8510b98c06e932ce7a6a9cbff4cbdded"},
  {"key": "space-mono-bold", "name": "Space Mono Bold", "style": "Fast bredde", "file": "SpaceMono-Bold.ttf", "sha256": "405e73d41afb7e5906efce206a326af5c956f38e255f35421c260e861e599c59"},
  {"key": "rubik-mono-one", "name": "Rubik Mono One", "style": "Brede blokbogstaver", "file": "RubikMonoOne-Regular.ttf", "sha256": "b22624b6a36e543e942106edfccd198c50b57b65b924bfe02a6f0a5699429e6b"},
];
export const FONT_BY_KEY = new Map(TEXT_FONTS.map(font => [font.key, font]));
