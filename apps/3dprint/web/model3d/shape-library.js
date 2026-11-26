import {MAX_PROJECT_OBJECTS} from './project.js';
import {BASIC_RECIPES} from './basic-recipes.js';
import {BUILDING_RECIPES} from './building-recipes.js';
import {CONNECTOR_PRESETS} from './connector-profiles.js';
import {INSPIRATION_RECIPES} from './inspiration-recipes.js';

const part = (kind, size, position = [0, 0, size[2] / 2], options = {}) => ({kind, size, position, ...options});
const cylinder = (diameter, height, position, options) => part('cylinder', [diameter, diameter, height], position, options);
const hole = {mode: 'hole'};
const recipeCard = ({id, label, icon}) => ({id, label, icon, configure: 'catalog-shape'});
const [washer, spacer, ...building] = BUILDING_RECIPES.map(recipeCard);

export const SHAPE_LIBRARY = {
  basic: BASIC_RECIPES.map(recipeCard),
  hardware: [
    washer,
    {id: 'nut', label: 'Møtrik', configure: 'nut'},
    {id: 'rod', label: 'Gevindstang', configure: 'rod'},
    spacer,
    {id: 'gear', label: 'Tandhjul', configure: 'gear'},
    {id: 'game-piece', label: 'Spillebrik', configure: 'game-piece'},
    ...building,
  ],
  everyday: [
    {id: 'cup', label: 'Kop', size: [117, 78, 90], parts: [
      cylinder(78, 90, [-19.5, 0, 45]),
      cylinder(50, 8, [33.5, 0, 51], {rotation: [-90, 0, 0]}),
      cylinder(70, 88.5, [-19.5, 0, 48.25], hole),
      cylinder(34, 10, [37, 0, 51], {...hole, rotation: [-90, 0, 0]}),
    ]},
    {id: 'toilet-roll', label: 'Toiletrulle', size: [115, 115, 95], color: '#ebe9e2', parts: [
      part('tube', [115, 115, 95], undefined, {params: {wall: 33.5}}),
      part('tube', [49, 49, 95], undefined, {params: {wall: 3.5}, color: '#a88461'}),
    ]},
    {id: 'phone', label: 'Mobiltelefon', size: [72, 151, 9], color: '#687d88', parts: [
      part('box', [72, 151, 8.3], undefined, {params: {round: 6}}),
      part('box', [66, 137, .9], [0, 0, 8.55], {params: {round: 4}, color: '#232d38'}),
      cylinder(2.5, .35, [0, 71, 8.325], {color: '#303b47'}),
    ]},
    {id: 'button', label: 'Knap', size: [16, 16, 3], parts: [
      cylinder(16, 3),
      ...[[-2.4, -2.4], [-2.4, 2.4], [2.4, -2.4], [2.4, 2.4]].map(([x, y]) => cylinder(2.4, 4, [x, y, 1.5], hole)),
    ]},
  ],
  connectors: CONNECTOR_PRESETS.map(preset => ({...preset, configure: 'connector'})),
  inspiration: INSPIRATION_RECIPES.map(recipe => ({...recipe, configure: 'catalog-shape'})),
};

// Parts are centred on XY and rest on local Z=0. A preset is one undoable group of editable shapes.
export function insertShapePreset(model, preset, position, bottom = 0, color = null) {
  if (!preset?.parts?.length || model.data.objects.length + preset.parts.length + 1 > MAX_PROJECT_OBJECTS
      || !Array.isArray(position) || position.length !== 2 || ![...position, bottom].every(value => Number.isFinite(value) && Math.abs(value) <= 1_000_000)
      || (color && !/^#[0-9a-f]{6}$/i.test(color))) return null;
  const children = preset.parts.map(source => {
    const child = structuredClone(source);
    child.id = crypto.randomUUID();
    if (!child.color && child.mode !== 'hole' && (color || preset.color)) child.color = color || preset.color;
    return child;
  });
  model.addMeshGroup(preset.label, children, [...position, bottom]);
  return model.selected[0];
}
