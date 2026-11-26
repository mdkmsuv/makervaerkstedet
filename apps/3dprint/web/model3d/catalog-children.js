import {connectorMount} from './connector-mount.js';
import {buildConnector, figureConnectorProfile} from './connector-profiles.js';
import {objectMatrix} from './scene.js';
import {stlAssetId} from './stl-import.js';

export function catalogChildren(shape, color, assets) {
  const centered = ([x, y, z]) => [x, y, z - shape.size[2] / 2];
  const parts = shape.parts.map(part => ({...part, id: crypto.randomUUID(), position: centered(part.position),
    ...(part.mode !== 'hole' && color && !part.color ? {color} : {})}));
  const mountColor = color || parts.find(part => part.mode !== 'hole')?.color;
  for (const mount of shape.connectors || []) {
    const face = objectMatrix({position: centered(mount.at), rotation: mount.rotation});
    const settings = {profile: figureConnectorProfile(mount.role, face), role: mount.role};
    const mesh = buildConnector(settings);
    const asset = stlAssetId(new Uint8Array(mesh.positions.buffer));
    assets.set(asset, mesh.positions);
    parts.push({...connectorMount(mesh, settings, face), id: crypto.randomUUID(), asset,
      ...(mount.role === 'plug' && mountColor ? {color: mountColor} : {})});
  }
  return parts;
}
