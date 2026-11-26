import {BASIC_RECIPES} from './basic-recipes.js';
import {BUILDING_RECIPES} from './building-recipes.js';
import {INSPIRATION_RECIPES} from './inspiration-recipes.js';

const kinds = [
  ['mesh', BASIC_RECIPES],
  ['group', BUILDING_RECIPES],
  ['group', INSPIRATION_RECIPES],
];

export const CATALOG_RECIPES = new Map(kinds.flatMap(([kind, recipes]) => recipes.map(recipe => [recipe.id, {...recipe, kind}])));
