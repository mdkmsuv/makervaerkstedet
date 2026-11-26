const percent = (key, label, value, min, max) => ({key, label: `${label} (%)`, value, min, max, step: 1, type: 'number'});

export const NATIVE_PROFILES = {
  pyramid: [percent('peakX', 'Spids vandret', 50, 10, 90), percent('peakY', 'Spids i dybden', 50, 10, 90)],
  roof: [percent('ridge', 'Tagryggens placering', 50, 10, 90)],
  wedge: [percent('ledge', 'Bredde på toppen', 0, 0, 85)],
  star: [{key: 'points', label: 'Antal spidser', value: 5, min: 3, max: 16, step: 1, type: 'number'}, percent('inner', 'Indre radius', 45, 20, 90)],
  heart: [percent('lobes', 'Mellemrum mellem buerne', 26, 12, 29), percent('tip', 'Spidsens runding', 1, 1, 14)],
};

export function validProfile(kind, params) {
  const profile = Object.hasOwn(NATIVE_PROFILES, kind) && NATIVE_PROFILES[kind];
  return Boolean(profile && params && typeof params === 'object' && !Array.isArray(params)
    && Object.keys(params).length > 0 && Object.entries(params).every(([key, value]) => {
      const field = profile.find(field => field.key === key);
      return field && Number.isInteger(value) && value >= field.min && value <= field.max;
    }));
}

export function profileValues(kind, params) {
  return Object.fromEntries(NATIVE_PROFILES[kind].map(({key, value}) => [key, params?.[key] ?? value]));
}
