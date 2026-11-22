(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const DEFAULT_WORKSPACE = { widthMm: 250, heightMm: 167 };
  const BOARD_MARGIN = 10;
  const MAX_WORKSPACE_SIZE = 390;
  const FILE_FORMAT = "laservaerkstedet";
  const ACCEPTED_FILE_FORMATS = new Set([FILE_FORMAT, "laservaerkstedet-test"]);
  const SUBMIT_URL = "/api/laser/submit";
  const FILE_VERSION = 2;
  const SUPPORTED_FILE_VERSIONS = new Set([1, 2]);
  const MAX_ROOT_OBJECTS = 100;
  // A 500-part laser drawing also needs room for its grouping nodes.
  // The same limits apply when editing and reopening the saved JSON.
  const MAX_TOTAL_OBJECTS = 600;
  const MAX_OBJECT_DEPTH = 6;
  const MAX_PROJECT_BYTES = 2_000_000;
  const PROJECT_SIZE_ERROR = "Tegningen bliver for stor til at kunne gemmes og åbnes igen. Fjern nogle detaljer, før du fortsætter. Din tegning er ikke ændret.";
  const zoomSteps = [25, 35, 50, 65, 75, 90, 100, 125, 150, 175, 200];
  const pathPattern = /^[\s\d.,+eEaAcChHlLmMqQsStTvVzZ-]+$/;

  const elements = {
    projectName: document.querySelector("#projectName"),
    objectLayer: document.querySelector("#objectLayer"),
    selectionOverlay: document.querySelector("#selectionOverlay"),
    designCanvas: document.querySelector("#designCanvas"),
    canvasScroll: document.querySelector("#canvasScroll"),
    emptyState: document.querySelector("#emptyState"),
    statusMessage: document.querySelector("#statusMessage"),
    undoButton: document.querySelector("#undoButton"),
    redoButton: document.querySelector("#redoButton"),
    copyButton: document.querySelector("#copyButton"),
    pasteButton: document.querySelector("#pasteButton"),
    deleteButton: document.querySelector("#deleteButton"),
    reviewButton: document.querySelector("#reviewButton"),
    selectedObjectName: document.querySelector("#selectedObjectName"),
    selectedObjectType: document.querySelector("#selectedObjectType"),
    selectedProcessBadge: document.querySelector("#selectedProcessBadge"),
    noSelectionMessage: document.querySelector("#noSelectionMessage"),
    selectionPanel: document.querySelector("#selectionPanel"),
    selectionHelp: document.querySelector("#selectionHelp"),
    textContentField: document.querySelector("#textContentField"),
    objectNameField: document.querySelector("#objectNameField"),
    objectRotationField: document.querySelector("#objectRotationField"),
    proportionRow: document.querySelector("#proportionRow"),
    proportionLabel: document.querySelector("#proportionLabel"),
    lockRatio: document.querySelector("#lockRatio"),
    processCut: document.querySelector("#processCut"),
    processEngrave: document.querySelector("#processEngrave"),
    groupButton: document.querySelector("#groupButton"),
    ungroupButton: document.querySelector("#ungroupButton"),
    desktopPartsList: document.querySelector("#desktopPartsList"),
    desktopPartsEmpty: document.querySelector("#desktopPartsEmpty"),
    zoomOutput: document.querySelector("#zoomOutput"),
    reviewDialog: document.querySelector("#reviewDialog"),
    reviewContent: document.querySelector("#reviewContent"),
    reviewSuccess: document.querySelector("#reviewSuccess"),
    reviewObjectLayer: document.querySelector("#reviewObjectLayer"),
    reviewProjectName: document.querySelector("#reviewProjectName"),
    reviewObjectCount: document.querySelector("#reviewObjectCount"),
    submitTestButton: document.querySelector("#submitTestButton"),
    cancelReviewButton: document.querySelector("#cancelReviewButton"),
    finishReviewButton: document.querySelector("#finishReviewButton"),
    newProjectDialog: document.querySelector("#newProjectDialog"),
    helpDialog: document.querySelector("#helpDialog"),
    fileErrorDialog: document.querySelector("#fileErrorDialog"),
    fileErrorMessage: document.querySelector("#fileErrorMessage"),
    fileInput: document.querySelector("#projectFileInput"),
    alignReference: document.querySelector("#alignReference"),
    alignButton: document.querySelector("#alignButton"),
    alignPanel: document.querySelector("#alignPanel"),
    alignHelp: document.querySelector("#alignHelp"),
    geometryMessage: document.querySelector("#geometryMessage"),
    geometryDisclosure: document.querySelector("#geometryDisclosure")
  };

  if (!window.paper || !window.WorkshopTextFonts) {
    elements.statusMessage.textContent = "Figurværktøjerne kunne ikke indlæses. Genåbn den kopierede projektmappe.";
    return;
  }

  const fontCatalog = window.WorkshopTextFonts;
  const textFonts = fontCatalog.createLoader(buffer => window.opentype.parse(buffer));
  const textFont = document.querySelector("#textFont");
  const textFontStatus = document.querySelector("#textFontStatus");
  let pendingFontChange = null;
  fontCatalog.addOptions(textFont);
  fontCatalog.registerPreviewFonts();

  const paperScope = new window.paper.PaperScope();
  paperScope.setup(document.createElement("canvas"));
  paperScope.settings.insertItems = false;

  const SHAPES = {
    rect: { label: "Firkant", width: 40, height: 30, icon: '<rect x="4" y="5" width="16" height="14" rx="1"/>' },
    rounded: { label: "Afrundet", width: 46, height: 30, path: "M15 0H85Q100 0 100 15V85Q100 100 85 100H15Q0 100 0 85V15Q0 0 15 0Z", icon: '<path transform="translate(3 3) scale(.18)" d="M15 0H85Q100 0 100 15V85Q100 100 85 100H15Q0 100 0 85V15Q0 0 15 0Z"/>' },
    circle: { label: "Cirkel", width: 30, height: 30, icon: '<circle cx="12" cy="12" r="8"/>' },
    ellipse: { label: "Oval", width: 42, height: 26, icon: '<ellipse cx="12" cy="12" rx="9" ry="6"/>' },
    triangle: { label: "Trekant", width: 34, height: 30, path: "M50 0L100 100H0Z", icon: '<path transform="translate(3 3) scale(.18)" d="M50 0L100 100H0Z"/>' },
    star: { label: "Stjerne", width: 34, height: 34, icon: '<path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>' },
    heart: { label: "Hjerte", width: 34, height: 32, path: "M50 100C38 88 0 62 0 30C0 -5 38 -8 50 18C62 -8 100 -5 100 30C100 62 62 88 50 100Z", icon: '<path transform="translate(3 3) scale(.18)" d="M50 100C38 88 0 62 0 30C0 -5 38 -8 50 18C62 -8 100 -5 100 30C100 62 62 88 50 100Z"/>' },
    diamond: { label: "Rombe", width: 28, height: 36, path: "M50 0L100 50 50 100 0 50Z", icon: '<path transform="translate(3 3) scale(.18)" d="M50 0L100 50 50 100 0 50Z"/>' },
    pentagon: { label: "Femkant", width: 34, height: 32, path: "M50 0L100 38 81 100 19 100 0 38Z", icon: '<path transform="translate(3 3) scale(.18)" d="M50 0L100 38 81 100 19 100 0 38Z"/>' },
    hexagon: { label: "Sekskant", width: 36, height: 32, path: "M25 0H75L100 50 75 100H25L0 50Z", icon: '<path transform="translate(3 3) scale(.18)" d="M25 0H75L100 50 75 100H25L0 50Z"/>' },
    arrow: { label: "Pil", width: 44, height: 26, path: "M0 30H60V0L100 50 60 100V70H0Z", icon: '<path transform="translate(3 3) scale(.18)" d="M0 30H60V0L100 50 60 100V70H0Z"/>' },
    tag: { label: "Mærke", width: 48, height: 28, path: "M20 0H100V100H20L0 50Z M14 50A5 5 0 1 0 24 50A5 5 0 1 0 14 50Z", icon: '<path transform="translate(3 3) scale(.18)" d="M20 0H100V100H20L0 50Z M14 50A5 5 0 1 0 24 50A5 5 0 1 0 14 50Z"/>' }
  };

  const LIBRARY_ITEMS = [
    { id: "cat", title: "Kat", category: "Dyr", path: "M18 39L12 6 37 24Q50 18 63 24L88 6 82 39Q98 82 65 92H35Q2 82 18 39Z M30 48H38 M62 48H70 M43 63L50 69 57 63 M50 69V77 M13 60L33 65 M67 65L87 60" },
    { id: "bird", title: "Fugl", category: "Dyr", path: "M8 60Q22 48 35 58Q32 26 53 22Q70 16 78 34L96 40 78 47Q81 77 53 82Q24 87 8 60Z M45 56Q62 43 64 66Q53 74 45 56Z M67 34H70" },
    { id: "fish", title: "Fisk", category: "Dyr", path: "M2 25L24 42Q61 2 97 50Q61 98 24 58L2 75Z M75 34Q63 50 75 66 M79 44H83" },
    { id: "butterfly", title: "Sommerfugl", category: "Dyr", path: "M48 44C10 0 -4 17 13 46C-5 70 12 96 47 62L50 88 53 62C88 96 105 70 87 46C104 17 90 0 52 44Z M50 46L39 17 M50 46L61 17" },
    { id: "leaf", title: "Blad", category: "Natur", path: "M14 85Q-10 10 90 5Q111 88 14 85Z M10 95L77 23 M31 73L23 44 M49 54L42 28 M49 54L76 58" },
    { id: "flower", title: "Blomst", category: "Natur", path: "M50 34C25 5 12 31 30 47C1 51 11 80 37 66C38 99 68 98 64 66C91 80 103 50 72 47C90 22 61 9 50 34Z M40 50A12 12 0 1 0 64 50A12 12 0 1 0 40 50Z" },
    { id: "tree", title: "Træ", category: "Natur", path: "M50 2L80 38H67L92 70H59V98H41V70H8L33 38H20Z" },
    { id: "sun", title: "Sol", category: "Natur", path: "M30 50A20 20 0 1 0 70 50A20 20 0 1 0 30 50Z M50 2V18 M50 82V98 M2 50H18 M82 50H98 M16 16L28 28 M72 72L84 84 M16 84L28 72 M72 28L84 16" },
    {
      id: "rocket",
      title: "Raket",
      category: "Rum",
      path: "M36 70Q22 32 50 2Q78 32 64 70Z M36 48L17 64 17 85 36 72 M64 48L83 64 83 85 64 72 M43 80L50 99 57 80 M41 35A9 9 0 1 0 59 35A9 9 0 1 0 41 35Z",
      parts: [
        { name: "Krop", path: "M36 70Q22 32 50 2Q78 32 64 70Z M41 35A9 9 0 1 0 59 35A9 9 0 1 0 41 35Z", fillRule: "evenodd" },
        { name: "Vinger", path: "M36 48L17 64L17 85L36 72Z M64 48L83 64L83 85L64 72Z", fillRule: "evenodd" },
        { name: "Flamme", path: "M43 80L50 99L57 80Z", fillRule: "evenodd" }
      ]
    },
    { id: "planet", title: "Planet", category: "Rum", path: "M22 50A28 28 0 1 0 78 50A28 28 0 1 0 22 50Z M22 37C-13 43 2 80 59 64C108 49 104 19 76 33 M21 49C-4 59 19 68 57 55C90 44 98 32 78 43" },
    { id: "music", title: "Musiknote", category: "Hobby", path: "M37 74V16L86 4V65 M37 30L86 18 M11 78A13 9 0 1 0 37 78A13 9 0 1 0 11 78Z M60 69A13 9 0 1 0 86 69A13 9 0 1 0 60 69Z" },
    { id: "bolt", title: "Lyn", category: "Hobby", path: "M57 0L16 57H44L35 100L88 37H58Z" }
  ];

  const typeLabels = Object.fromEntries(Object.entries(SHAPES).map(([key, value]) => [key, value.label]));
  Object.assign(typeLabels, { text: "Tekst", motif: "SVG-motiv", path: "Vektorform", group: "Gruppe" });

  const iconPaths = {
    text: '<path d="M5 5h14M12 5v14M8 19h8"></path>',
    motif: '<path d="M4 3h12l4 4v14H4z M16 3v5h4 M7 16l3-4 3 3 2-2 3 4"/>',
    path: '<path d="M4 17 8 7l5 8 3-10 4 12Z"/>',
    group: '<rect x="4" y="5" width="10" height="10"/><rect x="10" y="9" width="10" height="10"/>'
  };
  Object.entries(SHAPES).forEach(([key, value]) => { iconPaths[key] = value.icon; });

  function createKeyringProject() {
    return {
      name: "Min nøglering",
      workspace: { ...DEFAULT_WORKSPACE },
      isStarterExample: true,
      nextId: 4,
      selectedId: "part-1",
      selectedIds: ["part-1"],
      objects: [
        { id: "part-1", type: "rect", name: "Yderkant", x: 65, y: 58.5, width: 120, height: 50, rotation: 0, process: "cut", lockRatio: false, rounded: true },
        { id: "part-2", type: "circle", name: "Hul", x: 72.5, y: 79, width: 9, height: 9, rotation: 0, process: "cut", lockRatio: true },
        { id: "part-3", type: "text", name: "Tekst: MIN IDÉ", text: "MIN IDÉ", x: 97, y: 75.5, width: 58, height: 16, rotation: 0, process: "engrave", lockRatio: false }
      ]
    };
  }

  function createEmptyProject() {
    return { name: "Ny tegning", workspace: { ...DEFAULT_WORKSPACE }, nextId: 1, selectedId: null, selectedIds: [], objects: [] };
  }

  let state = createKeyringProject();
  let history = [];
  let future = [];
  let zoom = 100;
  let interactionState = null;
  let dialogReturnFocus = null;
  let suppressCanvasClick = false;
  let lastSelectionCount = -1;
  let objectClipboard = null;

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function round(value, digits = 1) {
    const factor = 10 ** digits;
    return Math.round(value * factor) / factor;
  }

  function formatNumber(value) {
    return Number.isInteger(value) ? String(value) : String(round(value));
  }

  function announce(message) {
    elements.statusMessage.textContent = message;
  }

  function normalizeSelection() {
    const rootIds = new Set(state.objects.map((object) => object.id));
    const source = Array.isArray(state.selectedIds) ? state.selectedIds : [state.selectedId].filter(Boolean);
    state.selectedIds = [...new Set(source)].filter((id) => rootIds.has(id));
    state.selectedId = state.selectedIds.at(-1) || null;
  }

  function setSelectedIds(ids) {
    if (state.repeatDuplicate && ids.slice().sort().join("|") !== state.selectedIds.slice().sort().join("|")) delete state.repeatDuplicate;
    state.selectedIds = [...new Set(ids)];
    normalizeSelection();
  }

  function selectedObjects() {
    const selected = new Set(state.selectedIds);
    return state.objects.filter((object) => selected.has(object.id));
  }

  function primarySelected() {
    return state.objects.find((object) => object.id === state.selectedId) || selectedObjects().at(-1) || null;
  }

  function allocateId() {
    const id = `part-${state.nextId}`;
    state.nextId += 1;
    return id;
  }

  function commit(message, mutation, feedback = elements.statusMessage) {
    const before = clone(state);
    mutation();
    normalizeSelection();
    if (projectFileBytes(state) > MAX_PROJECT_BYTES) {
      state = before;
      renderAll();
      feedback.textContent = PROJECT_SIZE_ERROR;
      return false;
    }
    history.push(before);
    if (history.length > 80) history.shift();
    future = [];
    renderAll();
    announce(message);
    return true;
  }

  function undo() {
    if (!history.length) return;
    future.push(clone(state));
    state = history.pop();
    normalizeSelection();
    renderAll();
    announce("Sidste ændring er fortrudt.");
  }

  function redo() {
    if (!future.length) return;
    history.push(clone(state));
    state = future.pop();
    normalizeSelection();
    renderAll();
    announce("Ændringen er gendannet.");
  }

  function svgElement(tag, attributes = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, String(value)));
    return node;
  }

  function paperPath(pathData) {
    return new paperScope.CompoundPath({ pathData, insert: false });
  }

  function validPathData(pathData) {
    if (typeof pathData !== "string" || !pathData || pathData.length > 150000 || !pathPattern.test(pathData)) return false;
    try {
      const item = paperPath(pathData);
      const bounds = item.bounds;
      const valid = [bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite)
        && Math.max(Math.abs(bounds.x), Math.abs(bounds.y), bounds.width, bounds.height) < 100000;
      item.remove();
      return valid;
    } catch {
      return false;
    }
  }

  function pathIsClosed(pathData) {
    try {
      const item = paperPath(pathData);
      const closed = item.children.length > 0 && item.children.every((child) => child.closed);
      item.remove();
      return closed;
    } catch {
      return false;
    }
  }

  function pathBounds(pathData) {
    const item = paperPath(pathData);
    const bounds = { x: item.bounds.x, y: item.bounds.y, width: item.bounds.width, height: item.bounds.height };
    item.remove();
    return bounds;
  }

  function normalizePathData(pathData) {
    const item = paperPath(pathData);
    const bounds = { x: item.bounds.x, y: item.bounds.y, width: item.bounds.width, height: item.bounds.height };
    if (bounds.width < 0.001 || bounds.height < 0.001) {
      item.remove();
      throw new Error("Figuren er for smal til at blive brugt.");
    }
    item.translate(new paperScope.Point(-bounds.x, -bounds.y));
    item.scale(100 / bounds.width, 100 / bounds.height, new paperScope.Point(0, 0));
    const normalized = item.pathData;
    item.remove();
    return { pathData: normalized, bounds };
  }

  function starPathData() {
    const points = [];
    for (let index = 0; index < 10; index += 1) {
      const radius = index % 2 === 0 ? 50 : 22.5;
      const angle = -Math.PI / 2 + index * Math.PI / 5;
      points.push(`${round(50 + Math.cos(angle) * radius, 4)},${round(50 + Math.sin(angle) * radius, 4)}`);
    }
    return `M${points.join("L")}Z`;
  }

  function roundedRectPathData(object) {
    if (!object.rounded) return "M0 0H100V100H0Z";
    const radiusX = Math.min(50, 800 / Math.max(object.width, 1));
    const radiusY = Math.min(50, 800 / Math.max(object.height, 1));
    return `M${radiusX} 0H${100 - radiusX}Q100 0 100 ${radiusY}V${100 - radiusY}Q100 100 ${100 - radiusX} 100H${radiusX}Q0 100 0 ${100 - radiusY}V${radiusY}Q0 0 ${radiusX} 0Z`;
  }

  function objectLocalPathData(object) {
    if (object.type === "rect") return roundedRectPathData(object);
    if (object.type === "circle" || object.type === "ellipse") return "M50 0A50 50 0 1 1 50 100A50 50 0 1 1 50 0Z";
    if (object.type === "star") return starPathData();
    if (object.type === "path") return object.pathData;
    if (object.type === "motif") return LIBRARY_ITEMS.find((item) => item.id === object.motifId)?.path || "";
    return SHAPES[object.type]?.path || "";
  }

  function objectIsClosed(object) {
    if (!object || object.type === "text") return false;
    if (object.type === "group") return object.children.length > 0 && object.children.every(objectIsClosed);
    const pathData = objectLocalPathData(object);
    return Boolean(pathData) && pathIsClosed(pathData);
  }

  function paperItemsInParent(object) {
    if (object.type === "text") return [];
    if (object.type === "group") {
      const items = object.children.flatMap((child) => paperItemsInParent(child));
      const pivot = new paperScope.Point(object.width / 2, object.height / 2);
      items.forEach((item) => {
        if (object.rotation) item.rotate(object.rotation, pivot);
        item.translate(new paperScope.Point(object.x, object.y));
      });
      return items;
    }
    const pathData = objectLocalPathData(object);
    if (!pathData) return [];
    const item = paperPath(pathData);
    item.fillRule = object.fillRule || "evenodd";
    item.scale(object.width / 100, object.height / 100, new paperScope.Point(0, 0));
    item.translate(new paperScope.Point(object.x, object.y));
    if (object.rotation) item.rotate(object.rotation, new paperScope.Point(object.x + object.width / 2, object.y + object.height / 2));
    return [item];
  }

  function paperItemForObject(object) {
    const items = paperItemsInParent(object);
    if (!items.length) throw new Error("Tekst og åbne streger kan ikke bruges med figurværktøjerne.");
    if (items.length === 1) return items[0];
    const pathData = items.map((item) => item.pathData).join(" ");
    items.forEach((item) => item.remove());
    const compound = paperPath(pathData);
    compound.fillRule = "evenodd";
    return compound;
  }

  function rotatePoint(point, center, degrees) {
    if (!degrees) return { x: point.x, y: point.y };
    const radians = degrees * Math.PI / 180;
    const cosine = Math.cos(radians);
    const sine = Math.sin(radians);
    const deltaX = point.x - center.x;
    const deltaY = point.y - center.y;
    return {
      x: center.x + deltaX * cosine - deltaY * sine,
      y: center.y + deltaX * sine + deltaY * cosine
    };
  }

  function objectBounds(object) {
    const center = { x: object.x + object.width / 2, y: object.y + object.height / 2 };
    const corners = [
      { x: object.x, y: object.y },
      { x: object.x + object.width, y: object.y },
      { x: object.x + object.width, y: object.y + object.height },
      { x: object.x, y: object.y + object.height }
    ].map((point) => rotatePoint(point, center, object.rotation || 0));
    const xs = corners.map((point) => point.x);
    const ys = corners.map((point) => point.y);
    const left = Math.min(...xs);
    const top = Math.min(...ys);
    const right = Math.max(...xs);
    const bottom = Math.max(...ys);
    return { x: left, y: top, width: right - left, height: bottom - top, left, top, right, bottom, centerX: (left + right) / 2, centerY: (top + bottom) / 2 };
  }

  function unionBounds(objects) {
    if (!objects.length) return null;
    const bounds = objects.map(objectBounds);
    const left = Math.min(...bounds.map((item) => item.left));
    const top = Math.min(...bounds.map((item) => item.top));
    const right = Math.max(...bounds.map((item) => item.right));
    const bottom = Math.max(...bounds.map((item) => item.bottom));
    return { x: left, y: top, width: right - left, height: bottom - top, left, top, right, bottom, centerX: (left + right) / 2, centerY: (top + bottom) / 2 };
  }

  function selectionFrame(objects = selectedObjects()) {
    if (!objects.length) return null;
    if (interactionState?.type === "rotate") return { ...interactionState.originalFrame, rotation: interactionState.originalFrame.rotation + interactionState.rotationDelta };
    if (objects.length === 1) {
      const object = objects[0];
      return { x: object.x, y: object.y, width: object.width, height: object.height, rotation: object.rotation || 0 };
    }
    const bounds = unionBounds(objects);
    return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height, rotation: 0 };
  }

  function frameWorldBounds(frame) {
    const temporary = { ...frame };
    return objectBounds(temporary);
  }

  function objectProcessValues(object) {
    if (object.type === "group") return object.children.flatMap(objectProcessValues);
    return [object.process];
  }

  function processSummary(objects) {
    const values = new Set(objects.flatMap(objectProcessValues));
    if (values.size === 1) return [...values][0];
    return "mixed";
  }

  function setProcessRecursive(object, process) {
    object.process = process;
    if (object.type === "group") object.children.forEach((child) => setProcessRecursive(child, process));
  }

  function objectTypeLabel(object) {
    if (object.type === "rect" && object.rounded) return "Afrundet firkant";
    if (object.type === "group") return `Gruppe med ${object.children.length} dele`;
    if (object.type === "path" && object.geometryKind === "combine") return "Kombineret kontur";
    if (object.type === "path" && object.geometryKind) return "Resultatform";
    return typeLabels[object.type] || "Del";
  }

  function objectTitle(object) {
    const process = processSummary([object]);
    const processText = process === "cut" ? "Skær, hele vejen igennem" : process === "engrave" ? "Gravér, på overfladen" : "Blandet Skær og Gravér";
    return `${object.name}. ${objectTypeLabel(object)}. ${processText}.`;
  }

  function createBaseGraphic(object) {
    let shape;
    if (object.type === "rect") {
      const radius = object.rounded ? Math.min(8, object.width / 2, object.height / 2) : 1;
      shape = svgElement("rect", { x: object.x, y: object.y, width: object.width, height: object.height, rx: radius, ry: radius });
    } else if (object.type === "circle" || object.type === "ellipse") {
      shape = svgElement("ellipse", { cx: object.x + object.width / 2, cy: object.y + object.height / 2, rx: object.width / 2, ry: object.height / 2 });
    } else if (object.type === "star") {
      const centerX = object.x + object.width / 2;
      const centerY = object.y + object.height / 2;
      const points = [];
      for (let index = 0; index < 10; index += 1) {
        const radiusX = (index % 2 === 0 ? 1 : 0.45) * object.width / 2;
        const radiusY = (index % 2 === 0 ? 1 : 0.45) * object.height / 2;
        const angle = -Math.PI / 2 + index * Math.PI / 5;
        points.push(`${centerX + Math.cos(angle) * radiusX},${centerY + Math.sin(angle) * radiusY}`);
      }
      shape = svgElement("polygon", { points: points.join(" "), "stroke-linejoin": "round" });
    } else if (object.type === "path" || object.type === "motif" || SHAPES[object.type]?.path) {
      const pathData = objectLocalPathData(object);
      shape = svgElement("path", {
        d: pathData,
        transform: `translate(${object.x} ${object.y}) scale(${object.width / 100} ${object.height / 100})`,
        "fill-rule": object.fillRule || "evenodd",
        "stroke-linejoin": "round",
        "stroke-linecap": "round"
      });
    } else {
      shape = svgElement("text", {
        x: object.x,
        y: object.y + object.height * 0.74,
        "font-family": fontCatalog.family(object.font) + ", Metrophobic, Helvetica, Arial, sans-serif",
        "font-size": object.height * 0.72,
        textLength: Math.max(1, object.width),
        lengthAdjust: "spacingAndGlyphs"
      });
      shape.textContent = object.text || "TEKST";
    }
    const line = object.closed === false || (object.process === "engrave" && object.engraveMode === "line");
    shape.setAttribute("class", `geometry-node process-${object.process}${line ? " line-geometry" : ""}`);
    return shape;
  }

  function makeObjectNode(object, interactive, nested = false) {
    let wrapper;
    if (object.type === "group") {
      wrapper = svgElement("g", { transform: `translate(${object.x} ${object.y}) rotate(${object.rotation || 0} ${object.width / 2} ${object.height / 2})` });
      object.children.forEach((child) => wrapper.append(makeObjectNode(child, false, true)));
    } else {
      const centerX = object.x + object.width / 2;
      const centerY = object.y + object.height / 2;
      wrapper = svgElement("g", { transform: `rotate(${object.rotation || 0} ${centerX} ${centerY})` });
      wrapper.append(createBaseGraphic(object));
    }
    if (!nested) {
      wrapper.setAttribute("class", "drawing-object");
      wrapper.setAttribute("data-object-id", object.id);
      if (interactive) {
        wrapper.setAttribute("tabindex", "0");
        wrapper.setAttribute("role", "button");
        wrapper.setAttribute("aria-label", objectTitle(object));
      } else {
        wrapper.setAttribute("aria-hidden", "true");
      }
    }
    return wrapper;
  }

  function preserveObjectFocus(container, update) {
    const active = document.activeElement;
    const current = container.contains(active) ? active.closest("[data-object-id]") : null;
    const focusedId = current?.dataset.objectId;
    const role = current?.dataset.focusRole || (active === current ? "button" : "");
    update();
    if (!focusedId) return;
    const selector = role ? `[data-object-id="${CSS.escape(focusedId)}"][data-focus-role="${role}"]` : `[data-object-id="${CSS.escape(focusedId)}"]`;
    const next = container.querySelector(selector)
      || container.querySelector(`[data-object-id="${CSS.escape(state.selectedId || "")}"]`)
      || document.querySelector("#selectToolButton");
    next?.focus({ preventScroll: true });
  }

  function renderObjects(layer, objects, interactive) {
    preserveObjectFocus(layer, () => {
      layer.replaceChildren();
      objects.forEach((object) => layer.append(makeObjectNode(object, interactive)));
    });
  }

  function canvasUnitsForPixels(pixels) {
    const matrix = elements.designCanvas.getScreenCTM();
    const scale = matrix ? Math.hypot(matrix.a, matrix.b) : 1;
    return pixels / Math.max(scale, 0.01);
  }

  function renderSelectionOverlay() {
    elements.selectionOverlay.replaceChildren();
    const objects = selectedObjects();
    if (interactionState?.type === "marquee" && interactionState.changed) {
      objects.forEach((object) => {
        elements.selectionOverlay.append(svgElement("rect", {
          class: "selection-box", x: object.x, y: object.y,
          width: object.width, height: object.height,
          transform: `rotate(${object.rotation || 0} ${object.x + object.width / 2} ${object.y + object.height / 2})`
        }));
      });
      const box = marqueeBounds(interactionState.startPoint, interactionState.currentPoint);
      elements.selectionOverlay.append(svgElement("rect", {
        id: "selectionMarquee", class: "selection-marquee",
        x: box.left, y: box.top, width: box.right - box.left, height: box.bottom - box.top
      }));
      return;
    }
    const frame = selectionFrame(objects);
    if (!frame) return;

    const centerX = frame.x + frame.width / 2;
    const centerY = frame.y + frame.height / 2;
    const frameGroup = svgElement("g", { transform: `rotate(${frame.rotation || 0} ${centerX} ${centerY})` });
    frameGroup.append(svgElement("rect", { class: "selection-box", x: frame.x, y: frame.y, width: frame.width, height: frame.height, rx: canvasUnitsForPixels(2) }));

    const hitSize = canvasUnitsForPixels(22);
    const visibleSize = canvasUnitsForPixels(9);
    const minimumSeparation = canvasUnitsForPixels(36);
    const padX = Math.max(0, (minimumSeparation - frame.width) / 2);
    const padY = Math.max(0, (minimumSeparation - frame.height) / 2);
    const corners = {
      nw: { x: frame.x - padX, y: frame.y - padY },
      ne: { x: frame.x + frame.width + padX, y: frame.y - padY },
      sw: { x: frame.x - padX, y: frame.y + frame.height + padY },
      se: { x: frame.x + frame.width + padX, y: frame.y + frame.height + padY }
    };
    Object.entries(corners).forEach(([corner, point]) => {
      if (padX || padY) {
        frameGroup.append(svgElement("line", {
          class: "resize-connector",
          x1: corner.includes("w") ? frame.x : frame.x + frame.width,
          y1: corner.includes("n") ? frame.y : frame.y + frame.height,
          x2: point.x, y2: point.y
        }));
      }
      frameGroup.append(svgElement("rect", {
        class: "resize-handle-hit",
        x: point.x - hitSize / 2,
        y: point.y - hitSize / 2,
        width: hitSize,
        height: hitSize,
        "data-resize-corner": corner
      }));
      frameGroup.append(svgElement("rect", {
        class: "resize-handle-visible",
        x: point.x - visibleSize / 2,
        y: point.y - visibleSize / 2,
        width: visibleSize,
        height: visibleSize,
        rx: visibleSize * 0.16
      }));
    });
    elements.selectionOverlay.append(frameGroup);

    const unit = canvasUnitsForPixels(1);
    const frameCenter = { x: centerX, y: centerY };
    const localStems = [
      { x: centerX, y: frame.y - padY, dx: 0, dy: -1 },
      { x: frame.x + frame.width + padX, y: centerY, dx: 1, dy: 0 },
      { x: frame.x - padX, y: centerY, dx: -1, dy: 0 },
      { x: centerX, y: frame.y + frame.height + padY, dx: 0, dy: 1 }
    ];
    const candidates = localStems.map((stem) => ({
      stem: rotatePoint(stem, frameCenter, frame.rotation || 0),
      point: rotatePoint({ x: stem.x + stem.dx * 34 * unit, y: stem.y + stem.dy * 34 * unit }, frameCenter, frame.rotation || 0)
    }));
    const viewport = elements.canvasScroll.getBoundingClientRect();
    const matrix = elements.designCanvas.getScreenCTM();
    const visibleHandle = (point) => {
      const screen = new DOMPoint(point.x, point.y).matrixTransform(matrix);
      return screen.x - 17 >= viewport.left && screen.x + 17 <= viewport.right && screen.y - 17 >= viewport.top && screen.y + 17 <= viewport.bottom;
    };
    const onPaper = (point) => point.x >= 17 * unit && point.x <= state.workspace.widthMm - 17 * unit && point.y >= 17 * unit && point.y <= state.workspace.heightMm - 17 * unit;
    const placement = candidates.find(({ point }) => onPaper(point) && visibleHandle(point)) || candidates.find(({ point }) => visibleHandle(point)) || candidates[0];
    const stemStart = placement.stem;
    const handle = placement.point;
    elements.selectionOverlay.append(svgElement("line", { class: "rotation-connector", x1: stemStart.x, y1: stemStart.y, x2: handle.x, y2: handle.y }));
    const rotationHandle = svgElement("g", { "data-rotate-handle": "", transform: `translate(${handle.x} ${handle.y})`, class: "rotation-handle" });
    const title = svgElement("title");
    title.textContent = "Træk for at dreje. Hold Shift for trin på 15°.";
    rotationHandle.append(title);
    rotationHandle.append(svgElement("circle", { r: 16 * unit, class: "rotation-handle-hit" }));
    rotationHandle.append(svgElement("circle", { r: 11 * unit, class: "rotation-handle-disc" }));
    rotationHandle.append(svgElement("path", { d: "M20 5v6h-6 M19 11a7 7 0 1 0-1 7", transform: `translate(${-8 * unit} ${-8 * unit}) scale(${16 * unit / 24})`, class: "rotation-handle-arrow" }));
    elements.selectionOverlay.append(rotationHandle);
    if (interactionState?.type === "rotate") {
      const angleText = svgElement("text", { x: handle.x > state.workspace.widthMm / 2 ? handle.x - 21 * unit : handle.x + 21 * unit, y: handle.y + 4 * unit, "text-anchor": handle.x > state.workspace.widthMm / 2 ? "end" : "start", class: "rotation-angle-label", "font-size": 13 * unit });
      angleText.textContent = `${formatNumber(objects.length === 1 ? objects[0].rotation : interactionState.rotationDelta)}°`;
      elements.selectionOverlay.append(angleText);
    }

    const worldBounds = frameWorldBounds({ ...frame, x:frame.x-padX, y:frame.y-padY, width:frame.width+padX*2, height:frame.height+padY*2 });
    const fontSize = canvasUnitsForPixels(13);
    const paddingX = canvasUnitsForPixels(7);
    const labelHeight = canvasUnitsForPixels(24);
    const labelText = `Bredde ${formatNumber(frame.width)} mm · Højde ${formatNumber(frame.height)} mm`;
    const labelWidth = Math.max(canvasUnitsForPixels(116), labelText.length * fontSize * 0.52 + paddingX * 2);
    const labelX = Math.min(state.workspace.widthMm - labelWidth / 2 - canvasUnitsForPixels(3), Math.max(labelWidth / 2 + canvasUnitsForPixels(3), worldBounds.centerX));
    const belowY = worldBounds.bottom + canvasUnitsForPixels(15) + labelHeight / 2;
    let labelY = belowY + labelHeight / 2 < state.workspace.heightMm ? belowY : Math.max(labelHeight / 2 + canvasUnitsForPixels(3), worldBounds.top - canvasUnitsForPixels(15) - labelHeight / 2);
    if (Math.abs(labelX - handle.x) < labelWidth / 2 + 18 * unit && Math.abs(labelY - handle.y) < labelHeight / 2 + 18 * unit) {
      const belowControls = Math.max(worldBounds.bottom + 15 * unit, handle.y + 26 * unit) + labelHeight / 2;
      const aboveControls = Math.min(worldBounds.top - 15 * unit, handle.y - 26 * unit) - labelHeight / 2;
      const belowScreen = new DOMPoint(labelX, belowControls + labelHeight / 2).matrixTransform(matrix);
      labelY = belowScreen.y < viewport.bottom - 3 ? belowControls : aboveControls;
    }
    const dimensionGroup = svgElement("g", { id: "selectionDimensions", "aria-hidden": "true" });
    dimensionGroup.append(svgElement("rect", {
      class: "dimension-label-bg",
      x: labelX - labelWidth / 2,
      y: labelY - labelHeight / 2,
      width: labelWidth,
      height: labelHeight,
      rx: canvasUnitsForPixels(4)
    }));
    const text = svgElement("text", { class: "dimension-label-text", x: labelX, y: labelY, "font-size": fontSize });
    text.textContent = labelText;
    dimensionGroup.append(text);
    elements.selectionOverlay.append(dimensionGroup);
  }

  function renderCanvas() {
    renderObjects(elements.objectLayer, state.objects, true);
    renderSelectionOverlay();
    elements.emptyState.hidden = state.objects.length !== 0;
  }

  function createPartIcon(type) {
    const icon = document.createElementNS(SVG_NS, "svg");
    icon.setAttribute("class", "od-icon");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("fill", "none");
    icon.setAttribute("stroke", "currentColor");
    icon.setAttribute("stroke-width", "1.8");
    icon.setAttribute("stroke-linecap", "round");
    icon.setAttribute("stroke-linejoin", "round");
    icon.innerHTML = iconPaths[type] || iconPaths.path;
    icon.setAttribute("aria-hidden", "true");
    return icon;
  }

  function renderPartList(container, emptyMessage) {
    preserveObjectFocus(container, () => {
      container.replaceChildren();
      emptyMessage.hidden = state.objects.length !== 0;
      state.objects.forEach((object) => {
        const row = document.createElement("div");
        row.className = "part-row";

        const selectionLabel = document.createElement("label");
        selectionLabel.className = "part-select-control";
        selectionLabel.dataset.objectId = object.id;
        selectionLabel.dataset.focusRole = "select";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = state.selectedIds.includes(object.id);
        checkbox.dataset.objectId = object.id;
        checkbox.dataset.focusRole = "select";
        checkbox.setAttribute("aria-label", `Vælg ${object.name} sammen med andre dele`);
        selectionLabel.append(checkbox);

        const button = document.createElement("button");
        button.type = "button";
        button.className = "part-button";
        button.dataset.objectId = object.id;
        button.dataset.focusRole = "button";
        button.setAttribute("aria-current", object.id === state.selectedId ? "true" : "false");
        button.setAttribute("aria-pressed", state.selectedIds.includes(object.id) ? "true" : "false");
        button.append(createPartIcon(object.type));

        const name = document.createElement("span");
        name.className = "part-name";
        name.textContent = object.name;
        button.append(name);

        const process = processSummary([object]);
        const processChip = document.createElement("span");
        processChip.className = `process-chip${process === "engrave" ? " engrave" : ""}`;
        processChip.textContent = process === "cut" ? "Skær" : process === "engrave" ? "Gravér" : "Blandet";
        button.append(processChip);
        row.append(selectionLabel, button);
        container.append(row);
      });
    });
  }

  function renderInspector() {
    const objects = selectedObjects();
    const primary = primarySelected();
    const frame = selectionFrame(objects);
    const hasSelection = objects.length > 0;
    elements.noSelectionMessage.hidden = hasSelection;
    elements.selectionPanel.hidden = !hasSelection;
    if (objects.length !== 1 || primary?.type !== "text" || pendingFontChange?.object !== primary) {
      pendingFontChange = null;
      textFont.value = fontCatalog.keyFor(primary?.font);
      showTextFontStatus("");
    }
    if (!hasSelection || !frame) return;

    const multi = objects.length > 1;
    const process = processSummary(objects);
    elements.selectedObjectName.textContent = multi ? `${objects.length} dele valgt` : primary.name;
    elements.selectedObjectType.textContent = multi ? "Samlet markering" : objectTypeLabel(primary);
    elements.selectedProcessBadge.textContent = process === "cut" ? "Skær" : process === "engrave" ? "Gravér" : "Blandet";
    elements.selectedProcessBadge.className = `selection-process-badge${process === "engrave" ? " engrave" : ""}`;

    const fields = {
      name: multi ? "Flere dele" : primary.name,
      text: !multi && primary.type === "text" ? primary.text : "",
      width: formatNumber(frame.width),
      height: formatNumber(frame.height),
      x: formatNumber(frame.x),
      y: formatNumber(frame.y),
      rotation: !multi ? formatNumber(primary.rotation || 0) : ""
    };
    Object.entries(fields).forEach(([property, value]) => {
      const field = document.querySelector(`[data-property="${property}"]`);
      if (field && document.activeElement !== field) field.value = value;
    });

    elements.textContentField.hidden = multi || primary.type !== "text";
    elements.objectNameField.hidden = multi;
    elements.objectRotationField.hidden = false;
    const rotationInput = document.querySelector("#objectRotation");
    rotationInput.disabled = multi;
    if (multi) rotationInput.value = "";
    rotationInput.placeholder = multi ? "Flere" : "";
    document.querySelector("#rotationHint").textContent = multi ? "De valgte dele drejes sammen." : "Træk i drejehåndtaget. Shift: trin på 15°.";
    elements.proportionRow.hidden = false;
    const forcedRatio = multi || primary.type === "group" || primary.type === "circle";
    elements.lockRatio.checked = forcedRatio || Boolean(primary.lockRatio);
    elements.lockRatio.disabled = forcedRatio;
    elements.proportionLabel.textContent = multi ? "Flere dele skaleres proportionalt" : primary.type === "group" ? "Grupper skaleres proportionalt" : primary.type === "circle" ? "Cirkler forbliver runde" : "Bevar proportioner";
    elements.processCut.checked = process === "cut";
    elements.processEngrave.checked = process === "engrave";
    elements.groupButton.disabled = objects.length < 2;
    elements.ungroupButton.disabled = objects.length !== 1 || primary.type !== "group";
    elements.selectionHelp.textContent = multi ? `${objects.length} dele flyttes, kopieres og slettes sammen.` : primary.type === "group" ? "Gruppen flyttes og ændrer størrelse som én del. Brug Ophæv gruppe for at redigere delene." : "Træk en ramme fra et tomt sted for at vælge flere dele. Hold Shift nede for at tilføje flere.";
  }

  function geometryEligibility() {
    const objects = selectedObjects();
    if (objects.length < 2) return { ok: false, message: "Vælg mindst to lukkede former." };
    if (objects.some((object) => object.type === "text")) return { ok: false, message: "Tekst skal først laves om til konturer i et rigtigt tegneprogram." };
    if (objects.some((object) => !objectIsClosed(object))) return { ok: false, message: "Valget indeholder åbne streger. Vælg kun lukkede former." };
    return { ok: true, message: `${objects.length} lukkede former er klar.` };
  }

  function renderControls() {
    const objects = selectedObjects();
    const hasSelection = objects.length > 0;
    elements.undoButton.disabled = history.length === 0;
    elements.redoButton.disabled = future.length === 0;
    elements.copyButton.disabled = !hasSelection;
    elements.pasteButton.disabled = !objectClipboard;
    elements.deleteButton.disabled = !hasSelection;
    elements.alignButton.disabled = !hasSelection;
    if (!hasSelection && !elements.alignPanel.hidden) closeAlignment(false);
    elements.reviewButton.disabled = state.objects.length === 0;
    document.querySelector("#resetRotationButton").disabled = !hasSelection;
    document.querySelector("#centerObjectButton").disabled = !hasSelection;
    elements.groupButton.disabled = objects.length < 2;
    elements.ungroupButton.disabled = objects.length !== 1 || objects[0]?.type !== "group";
    if (document.activeElement !== elements.projectName) elements.projectName.value = state.name;

    if (lastSelectionCount !== objects.length) {
      elements.alignReference.value = objects.length > 1 ? "selection" : "page";
      lastSelectionCount = objects.length;
    }
    const selectionReferenceInvalid = elements.alignReference.value === "selection" && objects.length < 2;
    document.querySelectorAll("[data-align]").forEach((button) => { button.disabled = !hasSelection || selectionReferenceInvalid; });
    elements.alignHelp.textContent = !hasSelection
      ? "Vælg en del for at justere den på tegnefladen."
      : selectionReferenceInvalid
        ? "Vælg mindst to dele, når De valgte dele er reference."
        : elements.alignReference.value === "page"
          ? "De valgte dele justeres i forhold til hele tegnefladen."
          : "Delene justeres inden for den samlede markering.";

    const eligibility = geometryEligibility();
    document.querySelectorAll("[data-boolean]").forEach((button) => { button.disabled = !eligibility.ok; });
    if (!elements.geometryMessage.classList.contains("error")) elements.geometryMessage.textContent = eligibility.message;
  }

  function renderAll() {
    renderWorkspace();
    renderCanvas();
    renderInspector();
    renderPartList(elements.desktopPartsList, elements.desktopPartsEmpty);
    renderControls();
  }

  function selectObject(id, options = {}) {
    if (!state.objects.some((object) => object.id === id)) return;
    if (options.additive) {
      const next = state.selectedIds.includes(id) ? state.selectedIds.filter((selectedId) => selectedId !== id) : [...state.selectedIds, id];
      setSelectedIds(next);
    } else {
      setSelectedIds([id]);
    }
    renderAll();
    if (options.announce !== false) {
      const count = state.selectedIds.length;
      announce(count > 1 ? `${count} dele er valgt.` : `${primarySelected()?.name || "Delen"} er valgt.`);
    }
  }

  function clearSelection() {
    setSelectedIds([]);
    renderAll();
    announce("Ingen dele er valgt.");
  }

  function makeUniqueName(baseName) {
    const names = new Set(state.objects.map((object) => object.name.toLocaleLowerCase("da")));
    if (!names.has(baseName.toLocaleLowerCase("da"))) return baseName;
    let number = 2;
    while (names.has(`${baseName} ${number}`.toLocaleLowerCase("da"))) number += 1;
    return `${baseName} ${number}`;
  }

  function addObject(type) {
    if (!allowObjectChange({ roots: state.objects.length + 1, total: countObjectTree(state.objects) + 1 })) return;
    const shape = SHAPES[type];
    const name = makeUniqueName(type === "text" ? "Ny tekst" : shape.label);
    const added = commit(`${name} er tilføjet.`, () => {
      const object = type === "text"
        ? { id: allocateId(), type, name, text: "NY TEKST", width: 54, height: 14, process: "engrave", lockRatio: false }
        : { id: allocateId(), type, name, width: shape.width, height: shape.height, process: "cut", lockRatio: ["circle", "star", "heart", "pentagon", "hexagon"].includes(type) };
      const offset = (state.nextId % 6) * 3;
      object.x = (state.workspace.widthMm - object.width) / 2 + offset;
      object.y = (state.workspace.heightMm - object.height) / 2 + offset;
      object.rotation = 0;
      if (type === "rounded") {
        object.type = "rect";
        object.rounded = true;
      }
      state.objects.push(object);
      setSelectedIds([object.id]);
    });
    if (added && type === "text") {
      const input = document.querySelector("#textContent");
      input.focus();
      input.select();
    }
  }

  const appCapabilities = window.ElevhubApp?.capabilities;
  const websiteLibrary = (Array.isArray(appCapabilities) ? appCapabilities :
    [...(appCapabilities?.required || []), ...(appCapabilities?.optional || [])]).includes("svg-library-v1");
  const librarySourceUrl = "https://xn--skolevrkstedet-5ib.dk/svg-bibliotek/";
  let websiteItems = null;
  let websiteVersion = null;
  let websitePage = 0;
  let websiteGeneration = 0;
  let websiteLoad = 0;
  let websiteLoading = false;
  let websiteTimer = null;
  const websiteControllers = new Set();
  const previewControllers = new Set();
  const websiteGeometry = new Map();

  function libraryIsOpen() { return document.querySelector("#libraryDialog").open; }

  async function libraryRequest(endpoint, previews = false) {
    const controller = new AbortController();
    const controllers = previews ? previewControllers : websiteControllers;
    controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 22000);
    try {
      const response = await fetch(`/api/laser/library${endpoint}`, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error("Biblioteket kunne ikke hentes. Prøv igen.");
      const data = await response.json();
      if (data.enabled !== true || !Number.isSafeInteger(data.version) || data.version < 0 || data.source_url !== librarySourceUrl) {
        throw new Error("Biblioteket er ikke tilgængeligt. Prøv igen.");
      }
      return data;
    } catch (error) {
      if (controller.signal.aborted) throw new Error("Bibliotekets hentning blev afbrudt. Prøv igen.");
      throw error;
    } finally {
      clearTimeout(timeout);
      controllers.delete(controller);
    }
  }

  function showLibrarySource(data, unavailable = false) {
    const lastSuccess = data?.last_success && new Date(data.last_success);
    const date = lastSuccess && Number.isFinite(lastSuccess.getTime()) ? ` Sidst hentet ${lastSuccess.toLocaleString("da-DK")}.` : "";
    const message = unavailable ? "Forbindelsen til biblioteket mangler. Allerede viste motiver bliver bevaret. Prøv igen." :
      data.cache_state === "stale" ? "Viser gemte motiver. Nye motiver kunne ikke hentes. Prøv igen." :
      data.cache_state === "ready" ? "Motiverne er hentet og gemt på denne computer." : "Der er endnu ingen gemte motiver. Henter biblioteket…";
    document.querySelector("#librarySourceStatus").textContent = `Kilde: skoleværkstedet.dk · ${message}${date}`;
    document.querySelector("#retryLibraryButton").hidden = !unavailable && data.cache_state === "ready";
  }

  function cancelLibraryRequests() {
    websiteLoad += 1;
    websiteGeneration += 1;
    websiteLoading = false;
    clearTimeout(websiteTimer);
    for (const controller of [...websiteControllers, ...previewControllers]) controller.abort();
  }

  async function refreshWebsiteLibrary(force = false) {
    if (!websiteLibrary || !libraryIsOpen() || websiteLoading) return;
    websiteLoading = true;
    const load = ++websiteLoad;
    const retryHadFocus = document.activeElement === document.querySelector("#retryLibraryButton");
    clearTimeout(websiteTimer);
    document.querySelector("#retryLibraryButton").disabled = true;
    try {
      const status = await libraryRequest("/status");
      if (load !== websiteLoad || !libraryIsOpen()) return;
      showLibrarySource(status);
      if (force || websiteItems === null || status.version !== websiteVersion) {
        const data = await libraryRequest("");
        if (load !== websiteLoad || !libraryIsOpen()) return;
        if (!Array.isArray(data.items) || data.items.length > 5000 || data.items.some(item =>
          !item || !/^[a-f0-9]{64}$/.test(item.id) || typeof item.title !== "string" || !item.title.trim() || item.title.length > 200 ||
          typeof item.category !== "string" || item.category.length > 200)) throw new Error("Bibliotekets liste kunne ikke læses. Prøv igen.");
        websiteItems = data.items;
        // Catalogue fetch can populate an initially empty cache (version 0→1).
        websiteVersion = data.version;
        websiteGeometry.clear();
        showLibrarySource(data);
        const select = document.querySelector("#libraryCategory");
        const selected = select.value;
        select.replaceChildren(new Option("Alle kategorier", ""), ...[...new Set(websiteItems.map(item => item.category))].sort((a,b) => a.localeCompare(b,"da")).map(name => new Option(name,name)));
        select.value = [...select.options].some(option => option.value === selected) ? selected : "";
        renderLibrary();
      }
    } catch (error) {
      if (load !== websiteLoad || !libraryIsOpen()) return;
      showLibrarySource(null, true);
      document.querySelector("#libraryStatus").textContent = `${error.message} Din tegning er bevaret.`;
      if (!websiteItems) renderLibrary();
    } finally {
      if (load === websiteLoad) {
        websiteLoading = false;
        document.querySelector("#retryLibraryButton").disabled = false;
        if (retryHadFocus && document.querySelector("#retryLibraryButton").hidden &&
          [document.body,document.querySelector("#retryLibraryButton")].includes(document.activeElement)) document.querySelector("#librarySearch").focus();
        if (libraryIsOpen()) websiteTimer = setTimeout(() => refreshWebsiteLibrary(), 15000);
      }
    }
  }

  function svgNumbers(value) {
    const values = String(value).match(/[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?/g) || [];
    if (String(value).replace(/[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?/g, "").replace(/[\s,]/g, "") || values.some(value => !Number.isFinite(Number(value)) || Math.abs(Number(value)) > 100000)) throw new Error("Motivet indeholder ugyldige mål.");
    return values.map(Number);
  }

  function svgTransform(value) {
    const matrix = new paperScope.Matrix();
    let rest = value || "";
    while (rest.trim()) {
      const match = /^\s*,?\s*(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)/.exec(rest);
      if (!match) throw new Error("Motivets placering kan ikke læses.");
      const n = svgNumbers(match[2]);
      let operation;
      if (match[1] === "matrix" && n.length === 6) operation = new paperScope.Matrix(...n);
      else if (match[1] === "translate" && [1,2].includes(n.length)) operation = new paperScope.Matrix(1,0,0,1,n[0],n[1] || 0);
      else if (match[1] === "scale" && [1,2].includes(n.length)) operation = new paperScope.Matrix(n[0],0,0,n.length === 2 ? n[1] : n[0],0,0);
      else if (match[1] === "rotate" && [1,3].includes(n.length)) { operation = new paperScope.Matrix(); operation.rotate(n[0], new paperScope.Point(n[1] || 0,n[2] || 0)); }
      else if (["skewX","skewY"].includes(match[1]) && n.length === 1) { const slope = Math.tan(n[0]*Math.PI/180); operation = new paperScope.Matrix(1,match[1] === "skewY" ? slope : 0,match[1] === "skewX" ? slope : 0,1,0,0); }
      else throw new Error("Motivets placering kan ikke læses.");
      matrix.append(operation);
      rest = rest.slice(match[0].length);
    }
    return matrix;
  }

  // Build geometry ourselves. No imported SVG node, style, link or script is
  // inserted into the document or handed to Paper's general SVG importer.
  const BOARD_LAYERS = new Map([["graver", "engrave-fill"], ["streg", "engrave-line"], ["skaer", "cut"]]);

  function decodeSvgParts(text, boardSize = 0) {
    if (typeof text !== "string" || new TextEncoder().encode(text).length > MAX_PROJECT_BYTES || /<!|<\?/.test(text)) throw new Error("Motivet kan ikke læses som en sikker vektorfigur.");
    const doc = new DOMParser().parseFromString(text, "image/svg+xml");
    const root = doc.documentElement;
    if (root.localName !== "svg" || root.namespaceURI !== SVG_NS || doc.querySelector("parsererror")) throw new Error("Motivet indeholder en ugyldig SVG.");
    const viewBox = svgNumbers(root.getAttribute("viewBox") || "");
    if (viewBox.length !== 4 || viewBox[2] <= 0 || viewBox[3] <= 0) throw new Error("Motivet mangler et gyldigt tegneområde.");
    if (boardSize && (viewBox.join(" ") !== `0 0 ${boardSize} ${boardSize}` ||
        ["width", "height"].some(axis => root.getAttribute(axis) !== `${boardSize}mm`))) {
      throw new Error("Spillepladens mål kunne ikke læses.");
    }
    const parts = [];
    let visited = 0;
    const allowed = new Set(["svg","g","path","rect","circle","ellipse","line","polyline","polygon","title","desc","defs"]);
    function visit(node, parentMatrix, inheritedRule, depth, operation) {
      if (++visited > 3000 || depth > 64 || node.namespaceURI !== SVG_NS || !allowed.has(node.localName) || (node.localName === "svg" && node !== root)) throw new Error("Motivet bruger en vektortype, som ikke understøttes.");
      for (const attribute of node.attributes) if (/^on|href$/i.test(attribute.name) || /url\s*\(|javascript:|@|[\\<>]/i.test(attribute.value)) throw new Error("Motivet indeholder henvisninger, som ikke understøttes.");
      const style = {};
      for (const declaration of (node.getAttribute("style") || "").split(";")) {
        if (!declaration.trim()) continue;
        const colon = declaration.indexOf(":");
        const key = declaration.slice(0,colon).trim();
        if (colon < 0 || !["fill","fill-rule","fill-opacity","stroke","stroke-width","stroke-linecap","stroke-linejoin","stroke-miterlimit","stroke-opacity","opacity"].includes(key)) throw new Error("Motivet bruger en effekt, som ikke understøttes.");
        style[key] = declaration.slice(colon+1).trim();
      }
      const rule = style["fill-rule"] || node.getAttribute("fill-rule") || inheritedRule;
      if (!["evenodd","nonzero"].includes(rule)) throw new Error("Motivets udfyldning kan ikke læses.");
      if (boardSize && node.hasAttribute("data-layer")) operation = BOARD_LAYERS.get(node.getAttribute("data-layer"));
      const matrix = parentMatrix.clone().append(svgTransform(node.getAttribute("transform")));
      if (["title","desc","defs"].includes(node.localName)) { if (node.children.length) throw new Error("Motivet indeholder skjulte figurer, som ikke understøttes."); return; }
      if (["svg","g"].includes(node.localName)) { for (const child of node.children) visit(child,matrix,rule,depth+1,operation); return; }
      const number = (name, fallback = 0) => { const n = svgNumbers(node.getAttribute(name) ?? String(fallback)); if (n.length !== 1) throw new Error("Motivet indeholder ugyldige mål."); return n[0]; };
      let item;
      try {
        const tag = node.localName;
        if (tag === "path") { const path = node.getAttribute("d"); if (!validPathData(path)) throw new Error("Motivets stier kan ikke læses."); item = paperPath(path); }
        else if (tag === "rect") {
          const width = number("width"), height = number("height");
          if (width <= 0 || height <= 0) throw new Error("Motivet indeholder en tom firkant.");
          const rx = number("rx",number("ry")), ry = number("ry",rx);
          if (rx < 0 || ry < 0) throw new Error("Motivets hjørner kan ikke læses.");
          item = new paperScope.Path.Rectangle({rectangle:new paperScope.Rectangle(number("x"),number("y"),width,height),radius:new paperScope.Size(Math.min(rx,width/2),Math.min(ry,height/2)),insert:false});
        } else if (["circle","ellipse"].includes(tag)) {
          const rx = tag === "circle" ? number("r") : number("rx"), ry = tag === "circle" ? rx : number("ry");
          if (rx <= 0 || ry <= 0) throw new Error("Motivet indeholder en tom ellipse.");
          item = new paperScope.Path.Ellipse({rectangle:new paperScope.Rectangle(number("cx")-rx,number("cy")-ry,2*rx,2*ry),insert:false});
        } else {
          const points = tag === "line" ? [number("x1"),number("y1"),number("x2"),number("y2")] : svgNumbers(node.getAttribute("points") || "");
          if (points.length < (tag === "polygon" ? 6 : 4) || points.length%2) throw new Error("Motivets punkter kan ikke læses.");
          item = new paperScope.Path({segments:Array.from({length:points.length/2},(_,i)=>new paperScope.Point(points[2*i],points[2*i+1])),closed:tag === "polygon",insert:false});
        }
        item.transform(matrix);
        const path = item.pathData;
        if (!validPathData(path) || !item.length || (item.bounds.width < .001 && item.bounds.height < .001)) throw new Error("Motivet indeholder tom eller for detaljeret geometri.");
        if (boardSize && !operation) throw new Error("En del af spillepladen mangler skæring eller gravering.");
        parts.push({name:`del ${parts.length+1}`,path,fillRule:rule, ...(operation ? { operation } : {})});
        if (!boardSize && parts.length > MAX_ROOT_OBJECTS) throw new Error("Motivet har for mange dele til én gruppe. Vælg et enklere motiv.");
      } finally { item?.remove(); }
      if (node.children.length) throw new Error("Motivet indeholder en figur, som ikke understøttes.");
    }
    visit(root,new paperScope.Matrix(),"nonzero",0);
    if (!parts.length) throw new Error("Motivet indeholder ingen brugbare vektorfigurer.");
    return parts;
  }

  async function websiteParts(item, preview = false) {
    if (websiteGeometry.has(item.id)) return websiteGeometry.get(item.id);
    const data = await libraryRequest(`/file/${item.id}`,preview);
    const parts = decodeSvgParts(data.svg);
    if (websiteGeometry.size >= 36) websiteGeometry.delete(websiteGeometry.keys().next().value);
    websiteGeometry.set(item.id,parts);
    return parts;
  }

  function renderLibrary() {
    const query = document.querySelector("#librarySearch").value.trim().toLocaleLowerCase("da");
    const category = document.querySelector("#libraryCategory").value;
    const filtered = (websiteLibrary ? websiteItems || [] : LIBRARY_ITEMS).filter((item) => (!category || item.category === category) && item.title.toLocaleLowerCase("da").includes(query));
    const grid = document.querySelector("#libraryGrid");
    const focusedMotif = grid.contains(document.activeElement) ? document.activeElement.dataset.motifId : null;
    const focusedPage = ["libraryNext","libraryPrevious"].includes(document.activeElement?.id) ? document.activeElement.id : null;
    grid.replaceChildren();
    const generation = ++websiteGeneration;
    for (const controller of previewControllers) controller.abort();
    const pageCount = Math.max(1,Math.ceil(filtered.length/12));
    websitePage = Math.min(websitePage,pageCount-1);
    document.querySelector("#libraryPages").hidden = !websiteLibrary || pageCount === 1;
    document.querySelector("#libraryPrevious").disabled = websitePage === 0;
    document.querySelector("#libraryNext").disabled = websitePage >= pageCount-1;
    document.querySelector("#libraryPageStatus").textContent = `Side ${websitePage+1} af ${pageCount}`;
    if (!websiteLibrary || websiteItems) document.querySelector("#libraryStatus").textContent = `${filtered.length} motiver · Tilføjes som grupperet Gravér.`;
    const previews = [];
    (websiteLibrary ? filtered.slice(websitePage*12,websitePage*12+12) : filtered).forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "library-card";
      button.dataset.motifId = item.id;
      const preview = svgElement("svg", { viewBox: "-8 -8 116 116", "aria-hidden": "true" });
      if (!websiteLibrary) preview.append(svgElement("path", { d: item.path, fill: "none", stroke: "currentColor", "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      const title = document.createElement("strong");
      title.textContent = item.title;
      const categoryLabel = document.createElement("small");
      categoryLabel.textContent = item.category;
      const insert = document.createElement("span");
      insert.className = "insert-label";
      insert.textContent = "+ Tilføj grupperet figur";
      button.append(preview, title, categoryLabel, insert);
      button.addEventListener("click", async () => {
        if (!websiteLibrary) { addLibraryMotif(item.id); return; }
        button.disabled = true;
        document.querySelector("#libraryStatus").textContent = `Henter ${item.title}…`;
        try {
          const parts = await websiteParts(item);
          if (!libraryIsOpen() || generation !== websiteGeneration) return;
          insertLibraryItem({...item,title:item.title.slice(0,60),parts});
        } catch (error) {
          if (libraryIsOpen() && generation === websiteGeneration) document.querySelector("#libraryStatus").textContent = `${error.message} Din tegning er ikke ændret. Vælg et andet motiv, eller prøv igen.`;
        } finally { button.disabled = false; }
      });
      grid.append(button);
      if (websiteLibrary) previews.push(async () => {
        try {
          const parts = await websiteParts(item,true);
          if (generation !== websiteGeneration || !libraryIsOpen()) return;
          const bounds = parts.map(part => pathBounds(part.path));
          const x = Math.min(...bounds.map(b=>b.x)), y = Math.min(...bounds.map(b=>b.y));
          const width = Math.max(...bounds.map(b=>b.x+b.width))-x, height = Math.max(...bounds.map(b=>b.y+b.height))-y;
          const margin = Math.max(width,height)*.06 || 1;
          preview.setAttribute("viewBox",`${x-margin} ${y-margin} ${Math.max(width,.01)+2*margin} ${Math.max(height,.01)+2*margin}`);
          parts.forEach(part=>preview.append(svgElement("path",{d:part.path,fill:"none",stroke:"currentColor","stroke-width":Math.max(width,height)/70 || .1,"fill-rule":part.fillRule})));
        } catch { if (generation === websiteGeneration) insert.textContent = "Se motiv ved at tilføje"; }
      });
    });
    if (!filtered.length) {
      const empty = document.createElement("p");
      empty.className = "library-empty";
      empty.textContent = websiteLibrary && !websiteItems ? "Henter motiver fra biblioteket. Ved forbindelsesfejl kan du prøve igen ovenfor." : "Ingen motiver matcher. Prøv et andet søgeord, eller nulstil filtrene.";
      grid.append(empty);
    }
    if (websiteLibrary) {
      let next = 0;
      const worker = async () => { while (next < previews.length && generation === websiteGeneration && libraryIsOpen()) await previews[next++](); };
      for (let i=0;i<Math.min(3,previews.length);i++) worker();
    }
    if (focusedMotif) ([...grid.querySelectorAll("button")].find(button => button.dataset.motifId === focusedMotif) || document.querySelector("#librarySearch")).focus();
    if (focusedPage && document.querySelector(`#${focusedPage}`).disabled) {
      const other = document.querySelector(focusedPage === "libraryNext" ? "#libraryPrevious" : "#libraryNext");
      (other.disabled ? document.querySelector("#librarySearch") : other).focus();
    }
  }

  function createLibraryGroup(item) {
    const parts = item.parts || [{ name: item.title, path: item.path, fillRule: "evenodd" }];
    const partBounds = parts.map((part) => pathBounds(part.path));
    const left = Math.min(...partBounds.map((bounds) => bounds.x));
    const top = Math.min(...partBounds.map((bounds) => bounds.y));
    const right = Math.max(...partBounds.map((bounds) => bounds.x + bounds.width));
    const bottom = Math.max(...partBounds.map((bounds) => bounds.y + bounds.height));
    const sourceWidth = Math.max(0.01, right - left);
    const sourceHeight = Math.max(0.01, bottom - top);
    const scale = 34 / Math.max(sourceWidth, sourceHeight);
    const width = Math.max(.1, round(sourceWidth * scale));
    const height = Math.max(.1, round(sourceHeight * scale));
    // A horizontal/vertical engraving line has zero extent on one axis.
    // Give its logical box a positive size while retaining constant path
    // coordinates on that axis, so saving and reopening preserves the line.
    const normalizedParts = parts.map(part => {
      const path = paperPath(part.path);
      try {
        const bounds = {x:path.bounds.x,y:path.bounds.y,width:path.bounds.width,height:path.bounds.height};
        if (![bounds.x,bounds.y,bounds.width,bounds.height].every(Number.isFinite)) throw new Error("Motivets mål kan ikke læses.");
        path.translate(new paperScope.Point(-bounds.x,-bounds.y));
        path.scale(100/(bounds.width || 1),100/(bounds.height || 1),new paperScope.Point(0,0));
        return {pathData:path.pathData,bounds};
      } finally { path.remove(); }
    });
    const children = parts.map((part, index) => {
      const normalized = normalizedParts[index];
      return {
        id: allocateId(),
        type: "path",
        name: `${item.title}: ${part.name || `del ${index + 1}`}`,
        x: round((normalized.bounds.x - left) * scale),
        y: round((normalized.bounds.y - top) * scale),
        width: Math.max(.1, round(normalized.bounds.width * scale)),
        height: Math.max(.1, round(normalized.bounds.height * scale)),
        rotation: 0,
        process: "engrave",
        lockRatio: true,
        pathData: normalized.pathData,
        fillRule: part.fillRule || "evenodd",
        closed: pathIsClosed(part.path),
        sourceMotifId: item.id
      };
    });
    return {
      id: allocateId(),
      type: "group",
      name: makeUniqueName(item.title),
      motifId: item.id,
      libraryGroup: true,
      x: round((state.workspace.widthMm - width) / 2),
      y: round((state.workspace.heightMm - height) / 2),
      width,
      height,
      rotation: 0,
      process: "engrave",
      lockRatio: true,
      children
    };
  }

  function addLibraryMotif(motifId) {
    const item = LIBRARY_ITEMS.find((candidate) => candidate.id === motifId);
    if (!item) return;
    insertLibraryItem(item);
  }

  function insertLibraryItem(item) {
    if (!allowObjectChange({ roots: state.objects.length + 1,
      total: countObjectTree(state.objects) + 1 + (item.parts?.length || 1) }, document.querySelector("#libraryStatus"))) return;
    if (!commit(`${item.title} er tilføjet som en grupperet Gravér-figur.`, () => {
      const object = createLibraryGroup(item);
      state.objects.push(object);
      setSelectedIds([object.id]);
    }, document.querySelector("#libraryStatus"))) return;
    document.querySelector("#libraryStatus").textContent = `${item.title} er tilføjet som gruppe. Vælg flere, eller tryk Tegn videre.`;
  }

  const boardDialog = document.querySelector("#boardDialog");
  const boardButton = document.querySelector("#boardButton");
  const boardPreview = document.querySelector("#boardPreview");
  const boardOptions = [...document.querySelectorAll("#boardOptions select")];
  const boardError = document.querySelector("#boardError");
  const boardInsert = document.querySelector("#insertBoardButton");
  const removeBoardExample = document.querySelector("#removeBoardExample");
  const boardGenerator = window.LaserBoardGenerator;
  const boardSettings = { ...boardGenerator.defaults };
  const boardPartNames = { cut: "Pladens kant", "engrave-line": "Graverede streger", "engrave-fill": "Graverede flader" };
  let boardDraft = null;

  function canRemoveBoardExample() {
    return state.isStarterExample && JSON.stringify(state.objects) === starterSignature &&
      !window.ElevhubProjectSession?.saveState?.().stored;
  }

  function renderBoardExampleChoice(reset = false) {
    const available = canRemoveBoardExample();
    document.querySelector("#boardExampleChoice").hidden = !available;
    if (reset) removeBoardExample.checked = available;
    document.querySelector("#boardOverlapNote").hidden = !state.objects.length || (available && removeBoardExample.checked);
  }

  function makeBoardLayers(board) {
    const parts = decodeSvgParts(board.svg, board.size);
    return Object.entries(boardPartNames).flatMap(([operation, name]) => {
      const paths = parts.filter(part => part.operation === operation);
      if (!paths.length) return [];
      // One compound path per operation, with the same physical frame for all layers.
      const geometry = paperPath(paths.map(part => part.path).join(" "));
      try {
        const size = board.size;
        geometry.scale(100 / size, 100 / size, new paperScope.Point(0, 0));
        const pathData = geometry.getPathData(null, 5);
        if (!validPathData(pathData)) throw new Error("Spillepladen er for detaljeret. Vælg en kortere bane.");
        return [{
          type: "path", name, x: 0, y: 0, width: size, height: size, rotation: 0, lockRatio: true,
          process: operation === "cut" ? "cut" : "engrave",
          ...(operation === "engrave-line" ? { engraveMode: "line" } : {}),
          pathData, fillRule: "nonzero", closed: geometry.children.every(path => path.closed)
        }];
      } finally { geometry.remove(); }
    });
  }

  function renderBoard() {
    boardOptions.forEach(field => { field.value = boardSettings[field.name]; });
    boardError.hidden = true;
    boardDraft = null;
    try {
      const board = boardGenerator.build(boardSettings, false);
      const layers = makeBoardLayers(board);
      boardPreview.setAttribute("viewBox", `0 0 ${board.size} ${board.size}`);
      boardPreview.replaceChildren(...layers.map(createBaseGraphic));
      document.querySelector("#boardSummary").textContent = `${board.size / 10} × ${board.size / 10} cm · ${board.n} felter · Variant ${boardSettings.seed}`;
      boardDraft = { size: board.size, layers };
    } catch (error) {
      boardPreview.replaceChildren();
      document.querySelector("#boardSummary").textContent = "";
      boardError.textContent = error.message || "Spillepladen kunne ikke dannes. Prøv en anden bane.";
      boardError.hidden = false;
    }
    boardInsert.disabled = !boardDraft;
  }

  function insertBoard() {
    if (!boardDraft) return;
    const { size, layers } = boardDraft;
    const removeExample = removeBoardExample.checked && canRemoveBoardExample();
    const objects = removeExample ? [] : state.objects;
    boardError.hidden = true;
    if (!allowObjectChange({ roots: objects.length + 1, total: countObjectTree(objects) + 1 + layers.length }, boardError)) {
      boardError.hidden = false;
      return;
    }
    const minimum = size + BOARD_MARGIN * 2;
    const message = removeExample ? "Spillepladen har erstattet nøgleringseksemplet." : "Spillepladen er sat ind som én gruppe.";
    if (!commit(message, () => {
      const widthMm = Math.max(state.workspace.widthMm, minimum);
      const heightMm = Math.max(state.workspace.heightMm, minimum);
      state.workspace = { widthMm, heightMm };
      const object = {
        id: allocateId(), type: "group", name: makeUniqueName("Spilleplade"),
        x: (widthMm - size) / 2, y: (heightMm - size) / 2, width: size, height: size,
        rotation: 0, process: "cut", lockRatio: true, libraryGroup: false,
        children: layers.map(layer => ({ ...layer, id: allocateId() }))
      };
      state.objects = [...objects, object];
      setSelectedIds([object.id]);
    }, boardError)) {
      boardError.hidden = false;
      return;
    }
    dialogReturnFocus = elements.objectLayer.querySelector(`[data-object-id="${state.selectedId}"]`);
    closeDialog(boardDialog);
    fitDrawing(false);
  }

  boardOptions.forEach(field => {
    field.replaceChildren(...boardGenerator.options[field.name].map(([value, label]) => new Option(label, value)));
    field.addEventListener("change", () => {
      boardSettings[field.name] = field.value;
      renderBoard();
    });
  });
  boardButton.addEventListener("click", () => {
    renderBoardExampleChoice(true);
    renderBoard();
    openDialog(boardDialog, boardOptions[0]);
  });
  removeBoardExample.addEventListener("change", () => renderBoardExampleChoice());
  document.querySelector("#newBoardVariantButton").addEventListener("click", () => {
    boardSettings.seed = boardSettings.seed % 999 + 1;
    renderBoard();
  });
  document.querySelector("#randomBoardButton").addEventListener("click", () => {
    for (const [key, options] of Object.entries(boardGenerator.options)) {
      if (key !== "stoerrelse") boardSettings[key] = options[Math.floor(Math.random() * options.length)][0];
    }
    boardSettings.seed = 1 + Math.floor(Math.random() * 999);
    renderBoard();
  });
  document.querySelector("#cancelBoardButton").addEventListener("click", () => closeDialog(boardDialog));
  document.querySelector("#closeBoardButton").addEventListener("click", () => closeDialog(boardDialog));
  boardDialog.addEventListener("close", handleDialogClose);
  boardInsert.addEventListener("click", insertBoard);

  function deleteSelected() {
    const objects = selectedObjects();
    if (!objects.length) return;
    const selected = new Set(objects.map((object) => object.id));
    commit(objects.length === 1 ? `${objects[0].name} er slettet.` : `${objects.length} dele er slettet.`, () => {
      const firstIndex = state.objects.findIndex((object) => selected.has(object.id));
      state.objects = state.objects.filter((object) => !selected.has(object.id));
      const fallback = state.objects[firstIndex] || state.objects[firstIndex - 1] || null;
      setSelectedIds(fallback ? [fallback.id] : []);
    });
  }

  function cloneWithFreshIds(object) {
    const copy = clone(object);
    copy.id = allocateId();
    if (copy.type === "group") copy.children = copy.children.map(cloneWithFreshIds);
    return copy;
  }

  function normalizeAngle(degrees) {
    const angle = round(((degrees % 360) + 360) % 360);
    return angle > 180 ? angle - 360 : angle;
  }

  function objectCenter(object) {
    return { x: object.x + object.width / 2, y: object.y + object.height / 2 };
  }

  function countObjectTree(objects) {
    return objects.reduce((total, object) => total + 1 + (object.type === "group" ? countObjectTree(object.children) : 0), 0);
  }

  function objectTreeDepth(objects) {
    return objects.reduce((depth, object) => Math.max(depth,
      object.type === "group" ? 1 + objectTreeDepth(object.children) : 0), 0);
  }

  function allowObjectChange({ roots = state.objects.length, total = countObjectTree(state.objects),
    depth = objectTreeDepth(state.objects) } = {}, feedback = elements.statusMessage) {
    const message = roots > MAX_ROOT_OBJECTS
      ? `Tegningen kan højst have ${MAX_ROOT_OBJECTS} dele på øverste niveau. Behold nogle dele i grupper, eller fjern nogle dele.`
      : total > MAX_TOTAL_OBJECTS
        ? `Tegningen kan højst indeholde ${MAX_TOTAL_OBJECTS} dele og grupper. Fjern nogle dele, før du tilføjer flere.`
        : depth > MAX_OBJECT_DEPTH
          ? `Der kan højst være ${MAX_OBJECT_DEPTH} niveauer i grupperne. Ophæv en gruppe, før du grupperer videre.`
          : "";
    if (!message) return true;
    feedback.textContent = message;
    return false;
  }

  function duplicateShapeSignature(object) {
    const shape = clone(object);
    function removeIdentity(part) {
      delete part.id;
      delete part.name;
      if (part.children) part.children.forEach(removeIdentity);
    }
    removeIdentity(shape);
    delete shape.x;
    delete shape.y;
    delete shape.rotation;
    return JSON.stringify(shape);
  }

  function repeatTransform(base, current) {
    if (base.length !== current.length || base.some((object, i) => duplicateShapeSignature(object) !== duplicateShapeSignature(current[i]))) return null;
    const angle = normalizeAngle((current[0].rotation || 0) - (base[0].rotation || 0));
    const rotated = rotatePoint(objectCenter(base[0]), { x: 0, y: 0 }, angle);
    const firstCenter = objectCenter(current[0]);
    const dx = firstCenter.x - rotated.x;
    const dy = firstCenter.y - rotated.y;
    const rigid = base.every((object, i) => {
      const expected = rotatePoint(objectCenter(object), { x: 0, y: 0 }, angle);
      const actual = objectCenter(current[i]);
      return Math.abs(expected.x + dx - actual.x) < 0.16
        && Math.abs(expected.y + dy - actual.y) < 0.16
        && Math.abs(normalizeAngle((current[i].rotation || 0) - (object.rotation || 0) - angle)) < 0.15;
    });
    return rigid ? { angle, dx, dy } : null;
  }

  function duplicateSelectedAndRepeat() {
    const objects = selectedObjects();
    if (!objects.length) {
      announce("Vælg en eller flere dele, før du trykker Ctrl+D.");
      return;
    }
    if (!allowObjectChange({ roots: state.objects.length + objects.length,
      total: countObjectTree(state.objects) + countObjectTree(objects) })) return;
    const chain = state.repeatDuplicate;
    let step = null;
    let continuing = Boolean(chain && objects.length === chain.copyIds.length && objects.every((object, i) => object.id === chain.copyIds[i]));
    if (continuing) {
      const untouched = objects.every((object, i) => JSON.stringify(object) === JSON.stringify(chain.placed[i]));
      step = untouched ? chain.step : repeatTransform(chain.base, objects);
      continuing = Boolean(step);
    }
    step ||= { angle: 0, dx: 0, dy: 0 };
    const names = objects.map((object, i) => continuing && object.name === chain.placed[i].name ? chain.names[i] : object.name.slice(0, 64));
    const positions = objects.map((object) => {
      const point = rotatePoint(objectCenter(object), { x: 0, y: 0 }, step.angle);
      return { x: round(point.x + step.dx - object.width / 2), y: round(point.y + step.dy - object.height / 2), rotation: normalizeAngle((object.rotation || 0) + step.angle) };
    });
    if (positions.some((point) => point.x < -state.workspace.widthMm * 2 || point.x > state.workspace.widthMm * 3 || point.y < -state.workspace.heightMm * 2 || point.y > state.workspace.heightMm * 3)) {
      announce("Kopien kommer for langt uden for tegnefladen. Flyt delene tættere på, og prøv igen.");
      return;
    }
    const base = clone(objects);
    const repeating = continuing && (Math.abs(step.angle) > 0.01 || Math.abs(step.dx) > 0.01 || Math.abs(step.dy) > 0.01);
    commit(repeating ? "Ny kopi med samme flytning og drejning." : "Kopi på samme sted. Flyt/drej, og tryk Ctrl+D igen.", () => {
      const copies = objects.map((object, i) => {
        const copy = cloneWithFreshIds(object);
        copy.name = makeUniqueName(`${names[i]} kopi`);
        Object.assign(copy, positions[i]);
        state.objects.push(copy);
        return copy;
      });
      setSelectedIds(copies.map((copy) => copy.id));
      // Session-only context follows undo/redo; projectPayload intentionally omits it.
      state.repeatDuplicate = { base, copyIds: copies.map((copy) => copy.id), placed: clone(copies), step, names };
    });
    focusPrimaryCanvasObject();
  }

  function applyRotation(objects, originals, center, degrees) {
    objects.forEach((object, index) => {
      const original = originals[index];
      const point = rotatePoint(objectCenter(original), center, degrees);
      object.x = round(point.x - original.width / 2);
      object.y = round(point.y - original.height / 2);
      object.rotation = normalizeAngle((original.rotation || 0) + degrees);
    });
  }

  function rotateSelected(degrees) {
    const objects = selectedObjects();
    if (!objects.length || Math.abs(degrees) < 0.01) return;
    const frame = selectionFrame(objects);
    const originals = clone(objects);
    const center = { x: frame.x + frame.width / 2, y: frame.y + frame.height / 2 };
    commit(`De valgte dele er drejet ${formatNumber(degrees)}°.`, () => applyRotation(objects, originals, center, degrees));
  }

  function editorShortcutTarget(target) {
    return !isTextEditingTarget(target) || Boolean(target?.matches?.('input[type="checkbox"], input[type="radio"]'));
  }

  function canUseObjectClipboard(event) {
    return !interactionState && !document.querySelector("dialog[open]") && editorShortcutTarget(event.target);
  }

  function selectAllObjects() {
    setSelectedIds(state.objects.map((object) => object.id));
    renderAll();
    if (state.selectedIds.length) focusPrimaryCanvasObject();
    else elements.designCanvas.focus({ preventScroll: true });
    announce(state.selectedIds.length ? `Alle ${state.selectedIds.length} dele er valgt.` : "Tegnefladen er tom.");
  }

  function captureObjectClipboard(cut = false, event = null) {
    const objects = selectedObjects();
    if (!objects.length) {
      announce("Vælg en eller flere dele først.");
      return;
    }
    const payload = { format: "laservaerkstedet-clipboard", version: 1, id: crypto.randomUUID(), cut, objects: clone(objects) };
    const text = JSON.stringify(payload);
    if (event?.clipboardData) event.clipboardData.setData("text/plain", text);
    objectClipboard = { text, objects: payload.objects, cut, pasteCount: 0 };
    if (cut) {
      deleteSelected();
      if (state.selectedIds.length) focusPrimaryCanvasObject();
      else elements.designCanvas.focus({ preventScroll: true });
    }
    renderControls();
    announce(`${objects.length} ${objects.length === 1 ? "del" : "dele"} ${cut ? "klippet" : "kopieret"}. Vælg Indsæt, eller tryk Ctrl+V.`);
  }

  function handleObjectCopy(event) {
    if (!canUseObjectClipboard(event)) return;
    event.preventDefault();
    captureObjectClipboard(event.type === "cut", event);
  }

  function copySelected() {
    const previous = objectClipboard;
    // Native copy events let browser shortcuts and the button use one clipboard flow.
    try { document.execCommand("copy"); } catch {}
    if (objectClipboard === previous) captureObjectClipboard();
  }

  function readObjectClipboard(text) {
    if (typeof text !== "string" || text.length > MAX_PROJECT_BYTES) throw new Error("Udklippet er for stort til tegningen.");
    const payload = JSON.parse(text);
    if (payload?.format !== "laservaerkstedet-clipboard" || payload.version !== 1 || !Array.isArray(payload.objects) || !payload.objects.length) {
      throw new Error("Kopiér først en eller flere dele i værkstedet.");
    }
    const validated = validateProjectFile({
      format: FILE_FORMAT, version: FILE_VERSION,
      workspace: { widthMm: state.workspace.widthMm, heightMm: state.workspace.heightMm },
      project: { name: "Udklip", nextId: 1, objects: payload.objects }
    });
    return { text, objects: validated.objects, cut: payload.cut === true, pasteCount: 0 };
  }

  function pasteCopiedObjects() {
    if (!objectClipboard) {
      announce("Kopiér eller klip en del først.");
      return;
    }
    const { objects, cut, pasteCount } = objectClipboard;
    if (!allowObjectChange({ roots: state.objects.length + objects.length,
      total: countObjectTree(state.objects) + countObjectTree(objects) })) return;
    const offset = 5 * (pasteCount + (cut ? 0 : 1));
    if (objects.some((object) => object.x + offset > state.workspace.widthMm * 3 || object.y + offset > state.workspace.heightMm * 3)) {
      announce("Kopierne kommer for langt uden for tegnefladen. Kopiér igen fra en placering tættere på.");
      return;
    }
    if (!commit(`${objects.length} ${objects.length === 1 ? "del er indsat" : "dele er indsat"}.`, () => {
      const copies = objects.map((object) => {
        const copy = cloneWithFreshIds(object);
        copy.x = round(object.x + offset);
        copy.y = round(object.y + offset);
        copy.name = makeUniqueName(cut ? object.name : `${object.name.slice(0, 64)} kopi`);
        state.objects.push(copy);
        return copy;
      });
      setSelectedIds(copies.map((copy) => copy.id));
    })) return;
    objectClipboard.pasteCount += 1;
    focusPrimaryCanvasObject();
  }

  function handleObjectPaste(event) {
    if (!canUseObjectClipboard(event)) return;
    event.preventDefault();
    const text = event.clipboardData?.getData("text/plain") || "";
    try {
      if (text && text !== objectClipboard?.text) objectClipboard = readObjectClipboard(text);
      if (!text && !objectClipboard) throw new Error("Kopiér eller klip en del først.");
      pasteCopiedObjects();
    } catch (error) {
      announce(error instanceof SyntaxError ? "Udklippet indeholder ikke dele fra værkstedet." : error.message);
    }
  }

  function moveSelected(deltaX, deltaY, message) {
    const objects = selectedObjects();
    if (!objects.length) return;
    commit(message || (objects.length === 1 ? `${objects[0].name} er flyttet.` : `${objects.length} dele er flyttet.`), () => {
      objects.forEach((object) => {
        object.x = round(object.x + deltaX);
        object.y = round(object.y + deltaY);
      });
    });
  }

  function setSelectedProcess(process) {
    const objects = selectedObjects();
    if (!objects.length || objects.every((object) => processSummary([object]) === process)) return;
    commit(`${objects.length === 1 ? objects[0].name : `${objects.length} dele`} er sat til ${process === "cut" ? "Skær" : "Gravér"}.`, () => {
      objects.forEach((object) => setProcessRecursive(object, process));
    });
  }

  function scaleChildTree(target, original, scaleX, scaleY) {
    target.x = round(original.x * scaleX);
    target.y = round(original.y * scaleY);
    target.width = round(original.width * scaleX);
    target.height = round(original.height * scaleY);
    if (target.type === "group") {
      target.children = clone(original.children);
      target.children.forEach((child, index) => scaleChildTree(child, original.children[index], scaleX, scaleY));
    }
  }

  function resizeObjectGeometry(target, original, width, height) {
    const scaleX = width / original.width;
    const scaleY = height / original.height;
    target.width = round(width);
    target.height = round(height);
    if (target.type === "group") {
      target.children = clone(original.children);
      target.children.forEach((child, index) => scaleChildTree(child, original.children[index], scaleX, scaleY));
    }
  }

  function fitProportionalSize(width, height, ratio) {
    let nextWidth = Math.max(1, width);
    let nextHeight = Math.max(1, height);
    const widthDriven = Math.abs(nextWidth / ratio - nextHeight) <= Math.abs(nextHeight * ratio - nextWidth);
    if (widthDriven) nextHeight = nextWidth / ratio;
    else nextWidth = nextHeight * ratio;
    if (nextWidth > state.workspace.widthMm) {
      nextWidth = state.workspace.widthMm;
      nextHeight = nextWidth / ratio;
    }
    if (nextHeight > state.workspace.heightMm) {
      nextHeight = state.workspace.heightMm;
      nextWidth = nextHeight * ratio;
    }
    return { width: Math.max(1, nextWidth), height: Math.max(1, nextHeight) };
  }

  function resizeSelectionFromTopLeft(targetWidth, targetHeight) {
    const objects = selectedObjects();
    const frame = selectionFrame(objects);
    if (!frame) return;
    const ratio = frame.width / frame.height;
    const size = fitProportionalSize(targetWidth, targetHeight, ratio);
    const scaleX = size.width / frame.width;
    const scaleY = size.height / frame.height;
    const originals = clone(objects);
    objects.forEach((object, index) => {
      const original = originals[index];
      const originalCenter = { x: original.x + original.width / 2, y: original.y + original.height / 2 };
      const nextCenter = { x: frame.x + (originalCenter.x - frame.x) * scaleX, y: frame.y + (originalCenter.y - frame.y) * scaleY };
      resizeObjectGeometry(object, original, original.width * scaleX, original.height * scaleY);
      object.x = round(nextCenter.x - object.width / 2);
      object.y = round(nextCenter.y - object.height / 2);
    });
  }

  function showTextFontStatus(message, error = false) {
    textFontStatus.textContent = message;
    textFontStatus.classList.toggle("error", error);
  }

  async function changeTextFont() {
    const object = primarySelected();
    if (selectedObjects().length !== 1 || object?.type !== "text") return;
    const key = textFont.value;
    const change = {object};
    pendingFontChange = change;
    try {
      const loading = textFonts.load(key);
      if (loading) {
        showTextFontStatus("Indlæser " + fontCatalog.details(key).name + " …");
        await loading;
      }
      if (pendingFontChange !== change) return;
      if (key !== fontCatalog.defaultKey) fontCatalog.checkGlyphs(textFonts.get(key), object.text, key);
      pendingFontChange = null;
      showTextFontStatus("");
      if (key === fontCatalog.keyFor(object.font)) return;
      commit("Skrifttypen er ændret.", () => {
        if (key === fontCatalog.defaultKey) delete object.font;
        else object.font = key;
      });
    } catch (error) {
      if (pendingFontChange !== change) return;
      pendingFontChange = null;
      textFont.value = fontCatalog.keyFor(object.font);
      showTextFontStatus(error.message, true);
    }
  }

  function handlePropertyChange(event) {
    const input = event.target.closest("[data-property]");
    const objects = selectedObjects();
    const primary = primarySelected();
    if (!input || !objects.length || !primary) return;
    const property = input.dataset.property;

    if (property === "lockRatio") {
      if (objects.length !== 1 || input.disabled) return;
      const checked = input.checked;
      commit(checked ? "Proportionerne bevares." : "Proportionerne kan ændres frit.", () => { primary.lockRatio = checked; });
      return;
    }

    if (property === "name" || property === "text") {
      if (objects.length !== 1) return;
      const value = property === "text" ? input.value.normalize("NFC").trim() : input.value.trim();
      if (!value) {
        input.value = primary[property] || primary.name;
        announce("Feltet må ikke være tomt.");
        return;
      }
      if (value === primary[property]) return;
      if (property === "text" && primary.font && textFonts.get(primary.font)) {
        try {
          fontCatalog.checkGlyphs(textFonts.get(primary.font), value, primary.font);
          if (!pendingFontChange) showTextFontStatus("");
        } catch (error) {
          showTextFontStatus(error.message, true);
          return;
        }
      }
      commit(property === "name" ? "Delen har fået et nyt navn." : "Teksten er ændret.", () => {
        primary[property] = value;
        if (property === "text" && primary.name.startsWith("Tekst:")) primary.name = `Tekst: ${value}`;
      });
      return;
    }

    const value = Number(input.value);
    if (!Number.isFinite(value)) {
      renderInspector();
      announce("Skriv et gyldigt tal.");
      return;
    }

    const frame = selectionFrame(objects);
    if (!frame) return;
    if (property === "width" || property === "height") {
      const maximum = property === "width" ? state.workspace.widthMm : state.workspace.heightMm;
      const nextValue = Math.min(maximum, Math.max(1, value));
      if (round(nextValue) === round(frame[property])) return;
      commit(`${objects.length === 1 ? primary.name : `${objects.length} dele`}: størrelsen er ændret.`, () => {
        if (objects.length > 1) {
          const ratio = frame.width / frame.height;
          const width = property === "width" ? nextValue : nextValue * ratio;
          const height = property === "height" ? nextValue : nextValue / ratio;
          resizeSelectionFromTopLeft(width, height);
          return;
        }
        const original = clone(primary);
        const forcedRatio = primary.type === "circle" || primary.type === "group" || primary.lockRatio;
        let width = property === "width" ? nextValue : original.width;
        let height = property === "height" ? nextValue : original.height;
        if (forcedRatio) {
          const ratio = original.width / original.height;
          if (property === "width") height = width / ratio;
          else width = height * ratio;
          const fitted = fitProportionalSize(width, height, ratio);
          width = fitted.width;
          height = fitted.height;
        }
        resizeObjectGeometry(primary, original, width, height);
      });
      return;
    }

    if (property === "x" || property === "y") {
      const minimum = property === "x" ? -state.workspace.widthMm : -state.workspace.heightMm;
      const maximum = property === "x" ? state.workspace.widthMm * 2 : state.workspace.heightMm * 2;
      const nextValue = Math.min(maximum, Math.max(minimum, value));
      const delta = nextValue - frame[property];
      if (!delta) return;
      commit(`${objects.length === 1 ? primary.name : `${objects.length} dele`}: placeringen er ændret.`, () => {
        objects.forEach((object) => { object[property] = round(object[property] + delta); });
      });
      return;
    }

    if (property === "rotation" && objects.length === 1) {
      const nextValue = normalizeAngle(Math.min(360, Math.max(-360, value)));
      rotateSelected(normalizeAngle(nextValue - (primary.rotation || 0)));
      input.value = formatNumber(primarySelected()?.rotation || 0);
    }
  }

  function pointInCanvas(event) {
    const point = elements.designCanvas.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const matrix = elements.designCanvas.getScreenCTM();
    return matrix ? point.matrixTransform(matrix.inverse()) : { x: 0, y: 0 };
  }

  function focusPrimaryCanvasObject() {
    const id = state.selectedId;
    if (!id) return;
    elements.objectLayer.querySelector(`[data-object-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
  }

  function marqueeBounds(start, end) {
    return {
      left: Math.min(start.x, end.x), top: Math.min(start.y, end.y),
      right: Math.max(start.x, end.x), bottom: Math.max(start.y, end.y)
    };
  }

  function beginMarquee(event) {
    if (interactionState || event.button !== 0 || event.target.closest("[data-object-id], [data-resize-corner], [data-rotate-handle]")) return;
    event.preventDefault();
    elements.designCanvas.focus({ preventScroll: true });
    const point = pointInCanvas(event);
    interactionState = {
      type: "marquee", pointerId: event.pointerId,
      startPoint: point, currentPoint: point,
      startClient: { x: event.clientX, y: event.clientY },
      before: clone(state), additive: event.shiftKey, changed: false
    };
    // Announce the final selection, not every pointer movement.
    elements.selectionPanel.querySelector(".selection-summary").setAttribute("aria-live", "off");
    try { elements.designCanvas.setPointerCapture(event.pointerId); } catch {}
  }

  function updateMarquee(event, point) {
    const interaction = interactionState;
    interaction.currentPoint = {
      x: Math.max(0, Math.min(state.workspace.widthMm, point.x)),
      y: Math.max(0, Math.min(state.workspace.heightMm, point.y))
    };
    if (!interaction.changed && Math.hypot(event.clientX - interaction.startClient.x, event.clientY - interaction.startClient.y) < 4) return;
    interaction.changed = true;
    const box = marqueeBounds(interaction.startPoint, interaction.currentPoint);
    const inside = state.objects.filter((object) => {
      const bounds = objectBounds(object);
      return bounds.left >= box.left && bounds.right <= box.right && bounds.top >= box.top && bounds.bottom <= box.bottom;
    }).map((object) => object.id);
    setSelectedIds(interaction.additive ? [...interaction.before.selectedIds, ...inside] : inside);
    renderAll();
  }

  function beginMove(event) {
    const target = event.target.closest("[data-object-id]");
    if (!target || event.button !== 0 || event.shiftKey) return;
    event.preventDefault();
    const id = target.dataset.objectId;
    if (!state.selectedIds.includes(id)) {
      setSelectedIds([id]);
      renderAll();
    }
    focusPrimaryCanvasObject();
    interactionState = {
      type: "move",
      pointerId: event.pointerId,
      startPoint: pointInCanvas(event),
      before: clone(state),
      changed: false
    };
    try { elements.designCanvas.setPointerCapture(event.pointerId); } catch {}
  }

  function frameCorner(frame, corner) {
    const point = {
      x: corner.includes("w") ? frame.x : frame.x + frame.width,
      y: corner.includes("n") ? frame.y : frame.y + frame.height
    };
    return rotatePoint(point, { x: frame.x + frame.width / 2, y: frame.y + frame.height / 2 }, frame.rotation || 0);
  }

  function beginRotation(event) {
    if (!event.target.closest("[data-rotate-handle]") || event.button !== 0 || interactionState) return;
    event.preventDefault();
    event.stopPropagation();
    const frame = selectionFrame();
    if (!frame) return;
    const center = { x: frame.x + frame.width / 2, y: frame.y + frame.height / 2 };
    const pointer = pointInCanvas(event);
    focusPrimaryCanvasObject();
    interactionState = {
      type: "rotate", pointerId: event.pointerId,
      before: clone(state), originalFrame: { ...frame }, center,
      startAngle: Math.atan2(pointer.y - center.y, pointer.x - center.x) * 180 / Math.PI,
      rotationDelta: 0, changed: false
    };
    try { elements.designCanvas.setPointerCapture(event.pointerId); } catch {}
  }

  function beginResize(event) {
    const handle = event.target.closest("[data-resize-corner]");
    if (!handle || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const frame = selectionFrame();
    if (!frame) return;
    const opposite = { nw: "se", ne: "sw", sw: "ne", se: "nw" }[handle.dataset.resizeCorner];
    const pointer = pointInCanvas(event);
    const realCorner = frameCorner(frame, handle.dataset.resizeCorner);
    interactionState = {
      type: "resize",
      pointerId: event.pointerId,
      corner: handle.dataset.resizeCorner,
      anchorWorld: frameCorner(frame, opposite),
      grabOffset: { x:pointer.x-realCorner.x, y:pointer.y-realCorner.y },
      originalFrame: frame,
      before: clone(state),
      changed: false
    };
    focusPrimaryCanvasObject();
    try { elements.designCanvas.setPointerCapture(event.pointerId); } catch {}
  }

  function calculateResizeFrame(interaction, pointer) {
    const original = interaction.originalFrame;
    const rotation = original.rotation || 0;
    const anchor = rotatePoint(interaction.anchorWorld, { x: 0, y: 0 }, -rotation);
    const adjustedPointer = { x:pointer.x-(interaction.grabOffset?.x || 0), y:pointer.y-(interaction.grabOffset?.y || 0) };
    const alignedPointer = rotatePoint(adjustedPointer, { x: 0, y: 0 }, -rotation);
    const signX = interaction.corner.includes("w") ? -1 : 1;
    const signY = interaction.corner.includes("n") ? -1 : 1;
    let width = Math.max(1, signX * (alignedPointer.x - anchor.x));
    let height = Math.max(1, signY * (alignedPointer.y - anchor.y));
    const objects = interaction.before.objects.filter((object) => interaction.before.selectedIds.includes(object.id));
    const primary = objects.find((object) => object.id === interaction.before.selectedId) || objects[0];
    const forceRatio = objects.length > 1 || primary.type === "group" || primary.type === "circle" || primary.lockRatio;
    if (forceRatio) {
      const scaleX = width / original.width;
      const scaleY = height / original.height;
      const scale = Math.max(1 / Math.max(original.width, original.height), Math.min(Math.max(scaleX, scaleY), state.workspace.widthMm / original.width, state.workspace.heightMm / original.height));
      width = original.width * scale;
      height = original.height * scale;
    } else {
      width = Math.min(state.workspace.widthMm, width);
      height = Math.min(state.workspace.heightMm, height);
    }
    const dragged = { x: anchor.x + signX * width, y: anchor.y + signY * height };
    const centerAligned = { x: (anchor.x + dragged.x) / 2, y: (anchor.y + dragged.y) / 2 };
    const centerWorld = rotatePoint(centerAligned, { x: 0, y: 0 }, rotation);
    return { x: centerWorld.x - width / 2, y: centerWorld.y - height / 2, width, height, rotation };
  }

  function applyResizeFrame(interaction, nextFrame) {
    state = clone(interaction.before);
    normalizeSelection();
    const objects = selectedObjects();
    const originals = interaction.before.objects.filter((object) => interaction.before.selectedIds.includes(object.id));
    const originalFrame = interaction.originalFrame;
    const scaleX = nextFrame.width / originalFrame.width;
    const scaleY = nextFrame.height / originalFrame.height;
    if (objects.length === 1) {
      const object = objects[0];
      const original = originals[0];
      object.x = round(nextFrame.x);
      object.y = round(nextFrame.y);
      resizeObjectGeometry(object, original, nextFrame.width, nextFrame.height);
      return;
    }
    objects.forEach((object) => {
      const original = originals.find((candidate) => candidate.id === object.id);
      const center = { x: original.x + original.width / 2, y: original.y + original.height / 2 };
      const nextCenter = {
        x: nextFrame.x + (center.x - originalFrame.x) * scaleX,
        y: nextFrame.y + (center.y - originalFrame.y) * scaleY
      };
      resizeObjectGeometry(object, original, original.width * scaleX, original.height * scaleY);
      object.x = round(nextCenter.x - object.width / 2);
      object.y = round(nextCenter.y - object.height / 2);
    });
  }

  function continueInteraction(event) {
    if (!interactionState || event.pointerId !== interactionState.pointerId) return;
    const point = pointInCanvas(event);
    if (interactionState.type === "marquee") {
      updateMarquee(event, point);
      return;
    }
    if (interactionState.type === "rotate") {
      const angle = Math.atan2(point.y - interactionState.center.y, point.x - interactionState.center.x) * 180 / Math.PI;
      const target = interactionState.originalFrame.rotation + normalizeAngle(angle - interactionState.startAngle);
      const snapped = event.shiftKey ? Math.round(target / 15) * 15 : Math.round(target);
      interactionState.rotationDelta = normalizeAngle(snapped - interactionState.originalFrame.rotation);
      state = clone(interactionState.before);
      normalizeSelection();
      const originals = selectedObjects().map(clone);
      applyRotation(selectedObjects(), originals, interactionState.center, interactionState.rotationDelta);
      interactionState.changed = Math.abs(interactionState.rotationDelta) > 0.01;
    } else if (interactionState.type === "move") {
      const deltaXRaw = point.x - interactionState.startPoint.x;
      const deltaYRaw = point.y - interactionState.startPoint.y;
      const snap = document.querySelector("#snapToGrid").checked;
      const deltaX = snap ? Math.round(deltaXRaw) : round(deltaXRaw);
      const deltaY = snap ? Math.round(deltaYRaw) : round(deltaYRaw);
      state = clone(interactionState.before);
      normalizeSelection();
      selectedObjects().forEach((object) => {
        const original = interactionState.before.objects.find((candidate) => candidate.id === object.id);
        object.x = round(original.x + deltaX);
        object.y = round(original.y + deltaY);
      });
      interactionState.changed = deltaX !== 0 || deltaY !== 0;
    } else {
      const nextFrame = calculateResizeFrame(interactionState, point);
      applyResizeFrame(interactionState, nextFrame);
      interactionState.changed = Math.abs(nextFrame.width - interactionState.originalFrame.width) > 0.05 || Math.abs(nextFrame.height - interactionState.originalFrame.height) > 0.05;
    }
    renderCanvas();
    renderInspector();
  }

  function finishInteraction(event) {
    if (!interactionState || event.pointerId !== interactionState.pointerId) return;
    const completed = interactionState;
    interactionState = null;
    if (elements.designCanvas.hasPointerCapture(event.pointerId)) elements.designCanvas.releasePointerCapture(event.pointerId);
    if (completed.type === "marquee") {
      if (!completed.changed && !completed.additive) setSelectedIds([]);
      suppressCanvasClick = true;
      renderAll();
      elements.selectionPanel.querySelector(".selection-summary").setAttribute("aria-live", "polite");
      if (state.selectedIds.length) focusPrimaryCanvasObject();
      else elements.designCanvas.focus({ preventScroll: true });
      announce(state.selectedIds.length ? `${state.selectedIds.length} ${state.selectedIds.length === 1 ? "del er valgt" : "dele er valgt"}.` : "Ingen dele er valgt.");
      return;
    }
    if (completed.changed) {
      if (projectFileBytes(state) > MAX_PROJECT_BYTES) {
        state = completed.before;
        suppressCanvasClick = true;
        renderAll();
        focusPrimaryCanvasObject();
        announce(PROJECT_SIZE_ERROR);
        return;
      }
      history.push(completed.before);
      if (history.length > 80) history.shift();
      future = [];
      suppressCanvasClick = true;
    }
    renderAll();
    focusPrimaryCanvasObject();
    if (completed.changed) announce(completed.type === "rotate" ? `De valgte dele er drejet ${formatNumber(completed.rotationDelta)}°.` : completed.type === "resize" ? "Størrelsen er ændret." : "De valgte dele er flyttet.");
  }

  function cancelInteraction(message = "Handlingen blev annulleret.") {
    if (!interactionState) return false;
    const canceled = interactionState;
    interactionState = null;
    state = canceled.before;
    normalizeSelection();
    if (canceled.type === "rotate") suppressCanvasClick = true;
    if (canceled.type === "marquee") {
      suppressCanvasClick = true;
      elements.selectionPanel.querySelector(".selection-summary").setAttribute("aria-live", "polite");
    }
    if (elements.designCanvas.hasPointerCapture(canceled.pointerId)) elements.designCanvas.releasePointerCapture(canceled.pointerId);
    renderAll();
    focusPrimaryCanvasObject();
    announce(message);
    return true;
  }

  function groupSelected() {
    const objects = selectedObjects();
    if (objects.length < 2) {
      announce("Vælg mindst to dele, der skal grupperes.");
      return;
    }
    const bounds = unionBounds(objects);
    const selected = new Set(objects.map((object) => object.id));
    if (!allowObjectChange({ roots: state.objects.length - objects.length + 1,
      total: countObjectTree(state.objects) + 1,
      depth: Math.max(objectTreeDepth(objects) + 1, objectTreeDepth(state.objects.filter((object) => !selected.has(object.id)))) })) return;
    const insertIndex = state.objects.findIndex((object) => selected.has(object.id));
    commit(`${objects.length} dele er grupperet.`, () => {
      const children = objects.map((object) => {
        const child = clone(object);
        child.x = round(child.x - bounds.x);
        child.y = round(child.y - bounds.y);
        return child;
      });
      state.objects = state.objects.filter((object) => !selected.has(object.id));
      const group = {
        id: allocateId(),
        type: "group",
        name: makeUniqueName("Gruppe"),
        x: round(bounds.x),
        y: round(bounds.y),
        width: round(bounds.width),
        height: round(bounds.height),
        rotation: 0,
        process: processSummary(objects) === "engrave" ? "engrave" : "cut",
        lockRatio: true,
        children
      };
      state.objects.splice(insertIndex, 0, group);
      setSelectedIds([group.id]);
    });
    focusPrimaryCanvasObject();
  }

  function ungroupSelected() {
    const group = primarySelected();
    if (selectedObjects().length !== 1 || !group || group.type !== "group") {
      announce("Vælg én gruppe for at ophæve den.");
      return;
    }
    if (!allowObjectChange({ roots: state.objects.length - 1 + group.children.length,
      total: countObjectTree(state.objects) - 1 })) return;
    const index = state.objects.findIndex((object) => object.id === group.id);
    commit(`${group.name} er opdelt i ${group.children.length} ${group.children.length === 1 ? "del" : "dele"}.`, () => {
      const groupCenter = { x: group.width / 2, y: group.height / 2 };
      const children = group.children.map((source) => {
        const child = clone(source);
        const center = { x: child.x + child.width / 2, y: child.y + child.height / 2 };
        const rotatedCenter = rotatePoint(center, groupCenter, group.rotation || 0);
        child.x = round(group.x + rotatedCenter.x - child.width / 2);
        child.y = round(group.y + rotatedCenter.y - child.height / 2);
        child.rotation = round((child.rotation || 0) + (group.rotation || 0));
        return child;
      });
      state.objects.splice(index, 1, ...children);
      setSelectedIds(children.map((child) => child.id));
    });
    focusPrimaryCanvasObject();
  }

  function closeAlignment(restoreFocus = true) {
    elements.alignPanel.hidden = true;
    elements.alignButton.setAttribute("aria-expanded", "false");
    if (restoreFocus && !elements.alignButton.disabled) elements.alignButton.focus({ preventScroll:true });
  }

  function openAlignment() {
    elements.alignPanel.hidden = false;
    elements.alignButton.setAttribute("aria-expanded", "true");
    requestAnimationFrame(() => elements.alignReference.focus({ preventScroll:true }));
  }

  function alignSelected(direction) {
    const objects = selectedObjects();
    if (!objects.length) return;
    const mode = elements.alignReference.value;
    if (mode === "selection" && objects.length < 2) {
      announce("Vælg mindst to dele for at justere efter De valgte dele.");
      return;
    }
    const reference = mode === "page"
      ? { left: 0, top: 0, right: state.workspace.widthMm, bottom: state.workspace.heightMm, centerX: state.workspace.widthMm / 2, centerY: state.workspace.heightMm / 2 }
      : unionBounds(objects);
    const labels = { left: "venstre", hcenter: "vandret midt", right: "højre", top: "top", vcenter: "lodret midt", bottom: "bund" };
    commit(`${objects.length === 1 ? objects[0].name : `${objects.length} dele`} er justeret ${labels[direction]}.`, () => {
      objects.forEach((object) => {
        const bounds = objectBounds(object);
        let deltaX = 0;
        let deltaY = 0;
        if (direction === "left") deltaX = reference.left - bounds.left;
        if (direction === "hcenter") deltaX = reference.centerX - bounds.centerX;
        if (direction === "right") deltaX = reference.right - bounds.right;
        if (direction === "top") deltaY = reference.top - bounds.top;
        if (direction === "vcenter") deltaY = reference.centerY - bounds.centerY;
        if (direction === "bottom") deltaY = reference.bottom - bounds.bottom;
        object.x = round(object.x + deltaX);
        object.y = round(object.y + deltaY);
      });
    });
  }

  function createResultObject(item, name, process, geometryKind) {
    const pathData = item.pathData;
    if (!pathData) throw new Error("Der er ingen figur tilbage. Flyt formerne og prøv igen.");
    const normalized = normalizePathData(pathData);
    if (normalized.bounds.width < 0.05 || normalized.bounds.height < 0.05) throw new Error("Der er ingen synlig figur tilbage. Flyt formerne og prøv igen.");
    return {
      id: null,
      type: "path",
      name: makeUniqueName(name),
      x: round(normalized.bounds.x),
      y: round(normalized.bounds.y),
      width: round(normalized.bounds.width),
      height: round(normalized.bounds.height),
      rotation: 0,
      process,
      lockRatio: false,
      pathData: normalized.pathData,
      fillRule: "evenodd",
      closed: true,
      geometryKind
    };
  }

  function setGeometryMessage(message, error = false) {
    elements.geometryMessage.textContent = message;
    elements.geometryMessage.classList.toggle("error", error);
    if (error) elements.geometryDisclosure.open = true;
    announce(message);
  }

  function performGeometry(kind) {
    const eligibility = geometryEligibility();
    if (!eligibility.ok) {
      setGeometryMessage(eligibility.message, true);
      return;
    }
    const objects = selectedObjects();
    const selected = new Set(objects.map((object) => object.id));
    const insertIndex = state.objects.findIndex((object) => selected.has(object.id));
    const process = objects[0].process;
    const names = {
      combine: "Kombineret form",
      unite: "Sammensat form",
      subtract: "Udskåret form",
      intersect: "Overlap",
      exclude: "Form uden overlap"
    };
    const messages = {
      combine: "Konturerne er kombineret. Indre konturer bliver til huller.",
      unite: "Formerne er sammensat til én geometri.",
      subtract: "De forreste former er trukket fra den bagerste.",
      intersect: "Kun overlapningen er bevaret.",
      exclude: "Overlapningen er fjernet."
    };
    try {
      const items = objects.map(paperItemForObject);
      let result;
      if (kind === "combine") {
        const pathData = items.map((item) => item.pathData).join(" ");
        items.forEach((item) => item.remove());
        result = paperPath(pathData);
        result.fillRule = "evenodd";
      } else {
        result = items.shift();
        result.fillRule = "evenodd";
        items.forEach((item) => {
          const next = result[kind](item, { insert: false });
          result.remove();
          item.remove();
          result = next;
          result.fillRule = "evenodd";
        });
      }
      const resultObject = createResultObject(result, names[kind], process, kind);
      result.remove();
      commit(messages[kind], () => {
        resultObject.id = allocateId();
        state.objects = state.objects.filter((object) => !selected.has(object.id));
        state.objects.splice(insertIndex, 0, resultObject);
        setSelectedIds([resultObject.id]);
      });
      elements.geometryMessage.classList.remove("error");
      elements.geometryMessage.textContent = messages[kind];
      focusPrimaryCanvasObject();
    } catch (error) {
      setGeometryMessage(error.message || "Figurværktøjet kunne ikke gennemføre ændringen.", true);
    }
  }

  function sizeCanvas() {
    elements.designCanvas.style.width = `${720 * zoom / 100}px`;
    elements.designCanvas.style.height = `${720 * state.workspace.heightMm / state.workspace.widthMm * zoom / 100}px`;
  }

  function renderWorkspace() {
    const { widthMm, heightMm } = state.workspace;
    const viewBox = `0 0 ${widthMm} ${heightMm}`;
    if (elements.designCanvas.getAttribute("viewBox") === viewBox) return;
    elements.designCanvas.setAttribute("viewBox", viewBox);
    document.querySelector("#reviewCanvas").setAttribute("viewBox", viewBox);
    elements.designCanvas.setAttribute("aria-label", `Tegneflade på ${widthMm} gange ${heightMm} millimeter`);
    document.querySelector("#workspaceSize").textContent = `${widthMm} × ${heightMm} mm`;
    document.querySelector("#objectWidth").max = widthMm;
    document.querySelector("#objectHeight").max = heightMm;
    const x = document.querySelector("#objectX");
    x.min = -widthMm;
    x.max = widthMm * 2;
    const y = document.querySelector("#objectY");
    y.min = -heightMm;
    y.max = heightMm * 2;
    sizeCanvas();
  }

  function setZoom(nextZoom) {
    zoom = Math.min(200, Math.max(25, Math.round(nextZoom)));
    sizeCanvas();
    elements.zoomOutput.value = `${zoom} %`;
    elements.zoomOutput.textContent = `${zoom} %`;
    renderSelectionOverlay();
  }

  function stepZoom(direction) {
    const currentIndex = zoomSteps.reduce((best, value, index) => Math.abs(value - zoom) < Math.abs(zoomSteps[best] - zoom) ? index : best, 0);
    setZoom(zoomSteps[Math.min(zoomSteps.length - 1, Math.max(0, currentIndex + direction))]);
  }

  function fitDrawing(announceFit = true) {
    const widthScale = (elements.canvasScroll.clientWidth - 48) / 720;
    const heightScale = (elements.canvasScroll.clientHeight - 48) / (720 * state.workspace.heightMm / state.workspace.widthMm);
    setZoom(Math.max(25, Math.floor(Math.min(widthScale, heightScale, 1.25) * 100 / 5) * 5));
    elements.canvasScroll.scrollTo({ top: 0, left: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    if (announceFit) announce("Hele tegningen vises.");
  }

  function openDialog(dialog, focusTarget, returnTarget = document.activeElement) {
    dialogReturnFocus = returnTarget;
    dialog.showModal();
    requestAnimationFrame(() => (focusTarget || dialog.querySelector("button, input, textarea"))?.focus());
  }

  function closeDialog(dialog) {
    dialog.close();
  }

  function handleDialogClose() {
    if (dialogReturnFocus && document.contains(dialogReturnFocus)) dialogReturnFocus.focus();
    dialogReturnFocus = null;
  }

  // ---- Aflevering til læreren -------------------------------------------
  // The drawing is sent as the school's outlined laser SVG dialect (see
  // clean_svg in app/laser_server.py): one <g data-operation> per process and
  // only <path> children. Text becomes outlines in its chosen font, so the laser
  // never depends on fonts. The same requestId is reused while the drawing and
  // the pupil's details are unchanged, so a retry never creates a second job.

  // An export problem the pupil can act on; other errors get a general message.
  class ExportProblem extends Error {}

  let lastSubmission = { signature: "", requestId: "" };
  let submitting = false;

  function textPaperItem(object, font) {
    const value = object.text || "TEKST";
    const size = object.height * 0.72;
    const pathData = fontCatalog.outline(font, value, 0, object.height * 0.74, size);
    if (!pathData) return null;
    const advance = font.getAdvanceWidth(value, size);
    const item = paperPath(pathData);
    // Some font contours return to their start point without a Z command.
    // Glyph outlines are always closed shapes, so mark them closed for filling.
    item.children.forEach((contour) => { contour.closed = true; });
    item.fillRule = "nonzero";
    // Same as the screen: <text textLength=width lengthAdjust=spacingAndGlyphs>.
    if (advance > 0) item.scale(Math.max(1, object.width) / advance, 1, new paperScope.Point(0, 0));
    item.translate(new paperScope.Point(object.x, object.y));
    if (object.rotation) item.rotate(object.rotation, new paperScope.Point(object.x + object.width / 2, object.y + object.height / 2));
    return item;
  }

  // Like paperItemsInParent, but keeps text and remembers each part's process.
  function exportItemsInParent(object) {
    if (object.type === "group") {
      const parts = object.children.flatMap(exportItemsInParent);
      const pivot = new paperScope.Point(object.width / 2, object.height / 2);
      parts.forEach(({ item }) => {
        if (object.rotation) item.rotate(object.rotation, pivot);
        item.translate(new paperScope.Point(object.x, object.y));
      });
      return parts;
    }
    if (object.type === "text") {
      const key = fontCatalog.keyFor(object.font);
      const font = textFonts.get(key);
      if (key !== fontCatalog.defaultKey) {
        try { fontCatalog.checkGlyphs(font, object.text || "TEKST", key); }
        catch (error) { throw new ExportProblem(error.message); }
      }
      const item = textPaperItem(object, font);
      return item ? [{ item, object }] : [];
    }
    return paperItemsInParent(object).map((item) => ({ item, object }));
  }

  function subpathsOf(item) {
    return item instanceof paperScope.CompoundPath ? item.children : [item];
  }

  function svgNumber(value) {
    return String(round(value, 2));
  }

  function escapeXml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&apos;" })[char]);
  }

  async function exportLaserSvg() {
    const keys = new Set();
    function collectFonts(object) {
      if (object.type === "text") keys.add(fontCatalog.keyFor(object.font));
      else if (object.type === "group") object.children.forEach(collectFonts);
    }
    state.objects.forEach(collectFonts);
    try { await Promise.all([...keys].map(key => textFonts.load(key))); }
    catch (error) { throw new ExportProblem(error.message); }
    const groups = { cut: [], "engrave-fill": [], "engrave-line": [] };
    const outside = [];
    let count = 0;
    state.objects.forEach((root) => {
      exportItemsInParent(root).forEach(({ item, object }) => {
        const bounds = item.bounds;
        if (bounds.left < -0.5 || bounds.top < -0.5 || bounds.right > state.workspace.widthMm + 0.5 || bounds.bottom > state.workspace.heightMm + 0.5) {
          if (!outside.includes(root.name)) outside.push(root.name);
        }
        const closed = [];
        const open = [];
        subpathsOf(item).forEach((path) => {
          if (!path.segments || path.segments.length < 2 && !path.closed) return;
          // Three decimals (1 µm) keeps detailed motifs under the server's path limit.
          (path.closed ? closed : open).push(path.getPathData(null, 3));
        });
        const name = escapeXml(object.name.slice(0, 60));
        const fillRule = item.fillRule === "nonzero" ? "nonzero" : "evenodd";
        const add = (operation, pathData) => {
          if (!pathData) return;
          if (pathData.length > 150000) throw new ExportProblem(`${object.name} er for detaljeret til laseren. Gør den enklere, eller del den op.`);
          count += 1;
          groups[operation].push(`<path d="${pathData}" data-operation="${operation}" data-name="${name}" fill-rule="${fillRule}"/>`);
        };
        if (object.process === "cut") {
          add("cut", closed.join(" "));
          add("cut", open.join(" "));
        } else if (object.engraveMode === "line") {
          add("engrave-line", [...closed, ...open].join(" "));
        } else {
          add("engrave-fill", closed.join(" "));
          add("engrave-line", open.join(" "));
        }
        item.remove();
      });
    });
    if (outside.length) {
      throw new ExportProblem(`Flyt ${outside.length === 1 ? "delen" : "delene"} ind på pladen, før du afleverer: ${outside.join(", ")}.`);
    }
    if (!count) throw new ExportProblem("Tegningen har ingen dele, laseren kan skære eller gravere.");
    if (count > 500) throw new ExportProblem("Tegningen har for mange dele til laseren. Saml nogle af delene, og prøv igen.");
    const body = Object.entries(groups)
      .filter(([, paths]) => paths.length)
      .map(([operation, paths]) => `<g data-operation="${operation}">${paths.join("")}</g>`)
      .join("");
    const width = svgNumber(state.workspace.widthMm);
    const height = svgNumber(state.workspace.heightMm);
    return `<svg xmlns="${SVG_NS}" version="1.1" width="${width}mm" height="${height}mm" viewBox="0 0 ${width} ${height}" data-laservaerksted="1">`
      + `<title>${escapeXml(state.name.slice(0, 60))}</title>${body}</svg>`;
  }

  function newRequestId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function reviewField(id) {
    return document.querySelector(id);
  }

  function rememberPupil(name, classroom) {
    try {
      sessionStorage.setItem("laservaerksted.pupil", JSON.stringify({ name, classroom }));
    } catch {}
  }

  function rememberedPupil() {
    try {
      const stored = JSON.parse(sessionStorage.getItem("laservaerksted.pupil") || "null");
      if (stored && typeof stored.name === "string" && typeof stored.classroom === "string") return stored;
    } catch {}
    return null;
  }

  function setReviewError(message) {
    const node = reviewField("#reviewError");
    node.textContent = message || "";
    node.hidden = !message;
    if (message) node.scrollIntoView({ block: "nearest" });
  }

  function setFieldError(field, show) {
    const error = reviewField(`#${field.id}Error`);
    error.hidden = !show;
    if (show) field.setAttribute("aria-invalid", "true");
    else field.removeAttribute("aria-invalid");
  }

  function setSubmitting(active) {
    submitting = active;
    elements.submitTestButton.disabled = active;
    elements.submitTestButton.textContent = active ? "Sender …" : "Aflever til læreren";
    elements.cancelReviewButton.disabled = active;
    document.querySelector("#closeReviewButton").disabled = active;
  }

  function renderReview() {
    renderObjects(elements.reviewObjectLayer, state.objects, false);
    elements.reviewProjectName.textContent = state.name;
    elements.reviewObjectCount.textContent = `${state.objects.length} ${state.objects.length === 1 ? "del" : "dele"}`;
    elements.reviewContent.hidden = false;
    elements.reviewSuccess.hidden = true;
    elements.submitTestButton.hidden = false;
    elements.cancelReviewButton.hidden = false;
    elements.finishReviewButton.hidden = true;
    setReviewError("");
    setSubmitting(false);
    document.querySelector("#reviewSubtitle").textContent = "Kontrollér tegningen, og skriv dit navn og din klasse.";
    setFieldError(reviewField("#reviewStudentName"), false);
    setFieldError(reviewField("#reviewClass"), false);
    const pupil = rememberedPupil();
    const nameField = reviewField("#reviewStudentName");
    const classField = reviewField("#reviewClass");
    if (pupil && !nameField.value) nameField.value = pupil.name;
    if (pupil && !classField.value) classField.value = pupil.classroom;
  }

  function openReview() {
    if (!state.objects.length) return;
    renderReview();
    const nameField = reviewField("#reviewStudentName");
    openDialog(elements.reviewDialog, nameField);
    requestAnimationFrame(() => nameField.select());
  }

  function showSubmissionResult(kind, jobId) {
    const heading = document.querySelector("#successHeading");
    const text = document.querySelector("#reviewSuccessText");
    const receipt = document.querySelector("#reviewReceipt");
    if (kind === "queued") {
      heading.textContent = "Afleveringen venter på forbindelse";
      text.textContent = "Der er ikke forbindelse til skolens server lige nu. Tegningen er gemt på computeren og bliver sendt af sig selv, når der er forbindelse igen.";
      receipt.textContent = "Sig det til læreren, så I kan se efter tegningen i laserkøen senere.";
    } else {
      heading.textContent = kind === "duplicate" ? "Tegningen er allerede afleveret" : "Afleveringen er sendt";
      text.textContent = kind === "duplicate" ? "Tegningen ligger allerede i lærerens laserkø. Der er ikke sendt en ekstra kopi." : "Din tegning ligger nu i lærerens laserkø.";
      receipt.textContent = jobId ? `Afleveringsnummer: ${jobId.slice(0, 8).toUpperCase()}. Sig nummeret til læreren, hvis I skal finde tegningen.` : "";
    }
    document.querySelector("#reviewSubtitle").textContent = kind === "queued" ? "Tegningen er gemt." : "Tegningen er afleveret.";
    elements.reviewContent.hidden = true;
    elements.reviewSuccess.hidden = false;
    elements.submitTestButton.hidden = true;
    elements.cancelReviewButton.hidden = true;
    elements.finishReviewButton.hidden = false;
    requestAnimationFrame(() => heading.focus());
  }

  async function submitDrawing() {
    if (submitting) return;
    const nameField = reviewField("#reviewStudentName");
    const classField = reviewField("#reviewClass");
    const pupilName = nameField.value.trim();
    const classroom = classField.value.trim();
    setFieldError(nameField, !pupilName);
    setFieldError(classField, !classroom);
    if (!pupilName || !classroom) {
      setReviewError("");
      const first = !pupilName ? nameField : classField;
      first.focus();
      reviewField(`#${first.id}Error`).scrollIntoView({ block: "nearest" });
      return;
    }
    setReviewError("");
    setSubmitting(true);
    let svg;
    try {
      svg = await exportLaserSvg();
    } catch (error) {
      setSubmitting(false);
      setReviewError(error instanceof ExportProblem
        ? error.message
        : "Tegningen kunne ikke gøres klar til laseren. Luk og åbn Laserværkstedet, og prøv igen.");
      return;
    }
    const project = (state.name || "Min tegning").trim().slice(0, 60) || "Min tegning";
    const signature = JSON.stringify([pupilName, classroom, project, svg]);
    if (signature !== lastSubmission.signature) lastSubmission = { signature, requestId: newRequestId() };
    rememberPupil(pupilName, classroom);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(SUBMIT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ elevnavn: pupilName, klasse: classroom, projekt: project, svg, requestId: lastSubmission.requestId }),
        signal: controller.signal,
        cache: "no-store"
      });
      let payload = null;
      try { payload = await response.json(); } catch {}
      if (response.status === 202 && payload?.queued) {
        setSubmitting(false);
        showSubmissionResult("queued");
        return;
      }
      if (response.ok && typeof payload?.id === "string") {
        setSubmitting(false);
        showSubmissionResult(payload.duplicate ? "duplicate" : "sent", payload.id);
        return;
      }
      const message = typeof payload?.error === "string" && payload.error ? payload.error : "";
      setSubmitting(false);
      if (response.status === 423) setReviewError(message || "Læreren har lukket for afleveringer lige nu. Gem projektet, og prøv igen senere.");
      else if (response.status === 413) setReviewError("Tegningen er for stor til at blive sendt. Fjern nogle dele, og prøv igen.");
      else setReviewError(message || "Tegningen kunne ikke afleveres. Prøv igen om lidt.");
    } catch {
      setSubmitting(false);
      setReviewError("Der kom ikke svar fra afleveringen. Prøv igen. Du sender ikke en ekstra kopi ved at prøve igen.");
    } finally {
      clearTimeout(timer);
    }
  }

  function projectPayload(projectState = state) {
    return {
      format: FILE_FORMAT,
      version: FILE_VERSION,
      workspace: projectState.workspace,
      project: {
        name: projectState.name,
        nextId: projectState.nextId,
        selectedIds: projectState.selectedIds,
        objects: projectState.objects
      }
    };
  }

  function projectFileBytes(projectState) {
    const payload = projectPayload(projectState);
    // Selection also appears in saved files. Reserve all current root IDs so
    // selecting more parts cannot make an otherwise valid file too large.
    payload.project.selectedIds = projectState.objects.map((object) => object.id);
    return new Blob([JSON.stringify(payload, null, 2)]).size;
  }

  function safeFilename(name) {
    const cleaned = name.toLocaleLowerCase("da-DK").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    return `${cleaned || "laserprojekt"}.laser.json`;
  }

  function saveProject() {
    const blob = new Blob([JSON.stringify(projectPayload(), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = safeFilename(state.name);
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    announce(`Projektfilen ${link.download} er hentet.`);
  }

  function finiteNumber(value, label, minimum, maximum) {
    if (!Number.isFinite(value) || value < minimum || value > maximum) throw new Error(`${label} har en ugyldig værdi.`);
    return round(value);
  }

  function validateProjectFile(data) {
    if (!data || !ACCEPTED_FILE_FORMATS.has(data.format) || !SUPPORTED_FILE_VERSIONS.has(Number(data.version))) throw new Error("Vælg en projektfil, der er gemt fra Laserværkstedet (.laser.json).");
    const widthMm = Number(data.workspace?.widthMm);
    const heightMm = Number(data.workspace?.heightMm);
    if (!Number.isInteger(widthMm) || widthMm < DEFAULT_WORKSPACE.widthMm || widthMm > MAX_WORKSPACE_SIZE ||
        !Number.isInteger(heightMm) || heightMm < DEFAULT_WORKSPACE.heightMm || heightMm > MAX_WORKSPACE_SIZE) {
      throw new Error("Projektfilen har ugyldige mål for tegnefladen.");
    }
    if (!data.project || typeof data.project !== "object") throw new Error("Projektoplysningerne mangler i filen.");
    const name = typeof data.project.name === "string" ? data.project.name.trim() : "";
    if (!name || name.length > 80) throw new Error("Projektnavnet mangler eller er for langt.");
    if (!Array.isArray(data.project.objects) || data.project.objects.length > MAX_ROOT_OBJECTS) throw new Error("Listen med dele er ugyldig eller for lang.");

    const ids = new Set();
    let totalObjects = 0;
    const allowedTypes = new Set(Object.keys(typeLabels));

    function validateObject(source, label, depth = 0) {
      if (!source || typeof source !== "object" || depth > MAX_OBJECT_DEPTH) throw new Error(`${label} kan ikke læses.`);
      totalObjects += 1;
      if (totalObjects > MAX_TOTAL_OBJECTS) throw new Error("Projektfilen indeholder for mange indlejrede dele.");
      if (!allowedTypes.has(source.type)) throw new Error(`${label} har en ukendt type.`);
      const id = typeof source.id === "string" && source.id.length <= 40 ? source.id : "";
      if (!id || ids.has(id)) throw new Error(`${label} har et ugyldigt id.`);
      ids.add(id);
      const objectName = typeof source.name === "string" ? source.name.trim() : "";
      if (!objectName || objectName.length > 80) throw new Error(`${label} mangler et gyldigt navn.`);
      if (source.process !== "cut" && source.process !== "engrave") throw new Error(`${objectName} har en ukendt handling.`);
      const object = {
        id,
        type: source.type,
        name: objectName,
        x: finiteNumber(Number(source.x), `${objectName}: X`, -widthMm * 2, widthMm * 3),
        y: finiteNumber(Number(source.y), `${objectName}: Y`, -heightMm * 2, heightMm * 3),
        width: finiteNumber(Number(source.width), `${objectName}: bredde`, 0.1, widthMm * 2),
        height: finiteNumber(Number(source.height), `${objectName}: højde`, 0.1, heightMm * 2),
        rotation: finiteNumber(Number(source.rotation || 0), `${objectName}: rotation`, -3600, 3600),
        process: source.process,
        lockRatio: Boolean(source.lockRatio)
      };
      if (source.type === "text") {
        const text = typeof source.text === "string" ? source.text.trim() : "";
        if (!text || text.length > 80) throw new Error(`${objectName} mangler en gyldig tekst.`);
        object.text = text;
        if (fontCatalog.keyFor(source.font) !== fontCatalog.defaultKey) object.font = source.font;
      }
      if (source.type === "motif") {
        if (!LIBRARY_ITEMS.some((item) => item.id === source.motifId)) throw new Error(`${objectName} har et ukendt SVG-motiv.`);
        object.motifId = source.motifId;
      }
      if (source.type === "path") {
        if (!validPathData(source.pathData)) throw new Error(`${objectName} indeholder ugyldig vektorgeometri.`);
        object.pathData = source.pathData;
        object.fillRule = source.fillRule === "nonzero" ? "nonzero" : "evenodd";
        object.closed = pathIsClosed(source.pathData);
        if (source.geometryKind && !["combine", "unite", "subtract", "intersect", "exclude"].includes(source.geometryKind)) throw new Error(`${objectName} har en ukendt figurtype.`);
        if (source.geometryKind && !object.closed) throw new Error(`${objectName} skal være en lukket resultatform.`);
        if (source.geometryKind) object.geometryKind = source.geometryKind;
        if (typeof source.sourceMotifId === "string") object.sourceMotifId = source.sourceMotifId;
        if (source.engraveMode === "line") object.engraveMode = "line";
      }
      if (source.type === "group") {
        if (!Array.isArray(source.children) || !source.children.length || source.children.length > MAX_ROOT_OBJECTS) throw new Error(`${objectName} har en ugyldig gruppeliste.`);
        object.children = source.children.map((child, index) => validateObject(child, `${objectName}, del ${index + 1}`, depth + 1));
        object.lockRatio = true;
        if (typeof source.motifId === "string" && (LIBRARY_ITEMS.some((item) => item.id === source.motifId) || /^[a-f0-9]{64}$/.test(source.motifId))) object.motifId = source.motifId;
        object.libraryGroup = Boolean(source.libraryGroup);
      }
      if (source.type === "rect") object.rounded = Boolean(source.rounded);
      if (source.type === "circle") object.lockRatio = true;
      return object;
    }

    const objects = data.project.objects.map((source, index) => validateObject(source, `Del ${index + 1}`));
    const rootIds = new Set(objects.map((object) => object.id));
    const requestedSelection = Array.isArray(data.project.selectedIds) ? data.project.selectedIds.filter((id) => rootIds.has(id)) : [];
    const largestNumericId = [...ids].reduce((largest, id) => {
      const match = id.match(/^part-(\d+)$/);
      return match ? Math.max(largest, Number(match[1])) : largest;
    }, 0);
    const selectedIds = requestedSelection.length ? [...new Set(requestedSelection)] : objects[0] ? [objects[0].id] : [];
    const validated = {
      name,
      workspace: { widthMm, heightMm },
      objects,
      selectedIds,
      selectedId: selectedIds.at(-1) || null,
      nextId: Math.max(largestNumericId + 1, Number(data.project.nextId) || 1)
    };
    if (projectFileBytes(validated) > MAX_PROJECT_BYTES) throw new Error("Projektfilen er for detaljeret til at kunne gemmes og åbnes igen. Vælg en mindre fil.");
    return validated;
  }

  async function openProjectFile(file) {
    try {
      if (!file || file.size > MAX_PROJECT_BYTES) throw new Error("Filen er for stor til Laserværkstedet.");
      const text = await file.text();
      const data = JSON.parse(text);
      const nextProject = validateProjectFile(data);
      commit(`${nextProject.name} er åbnet.`, () => { state = nextProject; });
      requestAnimationFrame(fitDrawing);
    } catch (error) {
      elements.fileErrorMessage.textContent = error instanceof SyntaxError ? "Filen indeholder ikke gyldig JSON. Prøv den oprindelige projektfil igen." : error.message;
      openDialog(elements.fileErrorDialog, document.querySelector("#closeFileErrorButton"), document.querySelector("#openProjectButton"));
      announce("Filen kunne ikke åbnes. Tegningen er ikke ændret.");
    } finally {
      elements.fileInput.value = "";
    }
  }

  function isTextEditingTarget(target) {
    return target instanceof HTMLElement && (target.matches("input, textarea, select") || target.isContentEditable);
  }

  function handleGlobalKeydown(event) {
    // The kiosk uses Alt+Left to return; browser navigation must not move a selected part.
    if (event.altKey && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    if (event.key === "Escape" && CSS.supports("selector(select:open)") && document.querySelector("select:open")) return;
    if (event.key === "Escape" && !elements.alignPanel.hidden) {
      event.preventDefault();
      closeAlignment();
      return;
    }
    if (event.target.closest?.("#alignPanel") && ["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Delete","Backspace"].includes(event.key)) return;
    if (event.key === "Escape" && cancelInteraction()) {
      event.preventDefault();
      return;
    }
    const modifier = event.ctrlKey || event.metaKey;
    const key = event.key.toLocaleLowerCase("da");
    if (modifier && !event.altKey && editorShortcutTarget(event.target) && !document.querySelector("dialog[open]")) {
      if (["c", "x", "v"].includes(key) && !event.shiftKey) {
        // Let native clipboard events supply clipboard data; never insert on Ctrl+C.
        if (interactionState) event.preventDefault();
        return;
      }
      if (["a", "d", "z", "y"].includes(key) && (!event.shiftKey || key === "z")) {
        event.preventDefault();
        if (interactionState) return;
        if (key === "a") selectAllObjects();
        else if (key === "d" && !event.repeat) duplicateSelectedAndRepeat();
        else if (key === "z") event.shiftKey ? redo() : undo();
        else if (key === "y") redo();
        return;
      }
    }
    if (interactionState || isTextEditingTarget(event.target) || document.querySelector("dialog[open]")) return;
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      deleteSelected();
      return;
    }
    const distance = event.shiftKey ? 5 : 1;
    const movements = { ArrowLeft: [-distance, 0], ArrowRight: [distance, 0], ArrowUp: [0, -distance], ArrowDown: [0, distance] };
    if (movements[event.key] && selectedObjects().length) {
      event.preventDefault();
      moveSelected(...movements[event.key]);
    }
  }

  elements.alignButton.addEventListener("click", () => elements.alignPanel.hidden ? openAlignment() : closeAlignment());
  document.querySelector("#closeAlignButton").addEventListener("click", () => closeAlignment());
  document.addEventListener("pointerdown", (event) => {
    if (!elements.alignPanel.hidden && !elements.alignPanel.contains(event.target) && !elements.alignButton.contains(event.target)) closeAlignment(false);
  });

  const libraryDialog = document.querySelector("#libraryDialog");
  document.querySelector("#libraryButton").addEventListener("click", () => {
    renderLibrary();
    openDialog(libraryDialog, document.querySelector("#librarySearch"));
    if (websiteLibrary) {
      document.querySelector("#librarySource").hidden = false;
      if (!websiteItems) {
        document.querySelector("#librarySourceStatus").textContent = "Kilde: skoleværkstedet.dk · Henter biblioteket…";
        document.querySelector("#libraryStatus").textContent = "Din tegning er bevaret, mens biblioteket hentes.";
      }
      refreshWebsiteLibrary(true);
      // Start previews only after the dialog has opened.
      if (websiteItems) renderLibrary();
    }
  });
  document.querySelector("#closeLibraryButton").addEventListener("click", () => closeDialog(libraryDialog));
  document.querySelector("#finishLibraryButton").addEventListener("click", () => closeDialog(libraryDialog));
  document.querySelector("#librarySearch").addEventListener("input", () => { websitePage = 0; renderLibrary(); });
  document.querySelector("#libraryCategory").addEventListener("change", () => { websitePage = 0; renderLibrary(); });
  document.querySelector("#retryLibraryButton").addEventListener("click", () => refreshWebsiteLibrary(true));
  document.querySelector("#libraryPrevious").addEventListener("click", () => { websitePage -= 1; renderLibrary(); });
  document.querySelector("#libraryNext").addEventListener("click", () => { websitePage += 1; renderLibrary(); });
  document.querySelector("#resetLibraryFilters").addEventListener("click", () => {
    document.querySelector("#librarySearch").value = "";
    document.querySelector("#libraryCategory").value = "";
    websitePage = 0;
    renderLibrary();
    document.querySelector("#librarySearch").focus();
  });
  libraryDialog.addEventListener("close", () => { cancelLibraryRequests(); handleDialogClose(); });

  document.querySelectorAll("[data-add-type]").forEach((button) => button.addEventListener("click", () => addObject(button.dataset.addType)));
  document.querySelector("#selectToolButton").addEventListener("click", () => announce("Træk en ramme fra et tomt sted. Dele helt indenfor bliver valgt. Hold Shift nede for at tilføje flere, eller brug fluebenene i listen."));
  elements.undoButton.addEventListener("click", undo);
  elements.redoButton.addEventListener("click", redo);
  elements.copyButton.addEventListener("click", copySelected);
  elements.pasteButton.addEventListener("click", pasteCopiedObjects);
  elements.deleteButton.addEventListener("click", deleteSelected);
  elements.groupButton.addEventListener("click", groupSelected);
  elements.ungroupButton.addEventListener("click", ungroupSelected);
  document.querySelector("#restoreExampleButton").addEventListener("click", () => commit("Eksemplet Min nøglering er lagt på tegnefladen.", () => { state = createKeyringProject(); }));

  elements.designCanvas.addEventListener("pointerdown", () => {
    if (!interactionState) suppressCanvasClick = false;
  }, true);
  elements.designCanvas.addEventListener("pointerdown", beginMarquee);
  elements.objectLayer.addEventListener("pointerdown", beginMove);
  elements.selectionOverlay.addEventListener("pointerdown", beginRotation);
  elements.selectionOverlay.addEventListener("pointerdown", beginResize);
  elements.designCanvas.addEventListener("pointermove", continueInteraction);
  elements.designCanvas.addEventListener("pointerup", finishInteraction);
  elements.designCanvas.addEventListener("pointercancel", () => cancelInteraction("Handlingen blev afbrudt og er rullet tilbage."));
  elements.designCanvas.addEventListener("lostpointercapture", () => {
    if (interactionState) cancelInteraction("Handlingen blev afbrudt og er rullet tilbage.");
  });
  elements.objectLayer.addEventListener("click", (event) => {
    event.stopPropagation();
    if (suppressCanvasClick) {
      suppressCanvasClick = false;
      return;
    }
    const target = event.target.closest("[data-object-id]");
    if (target) selectObject(target.dataset.objectId, { additive: event.shiftKey, announce: false });
  });
  elements.objectLayer.addEventListener("keydown", (event) => {
    const target = event.target.closest("[data-object-id]");
    if (target && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      selectObject(target.dataset.objectId, { additive: event.shiftKey });
    }
  });
  elements.designCanvas.addEventListener("click", (event) => {
    if (suppressCanvasClick) {
      suppressCanvasClick = false;
      return;
    }
    if (!event.target.closest("[data-object-id], [data-resize-corner], [data-rotate-handle]")) clearSelection();
  });

  elements.desktopPartsList.addEventListener("click", (event) => {
    const checkbox = event.target.closest('input[type="checkbox"][data-object-id]');
    if (checkbox) {
      selectObject(checkbox.dataset.objectId, { additive: true });
      return;
    }
    const button = event.target.closest("button[data-object-id]");
    if (button) selectObject(button.dataset.objectId, { additive: event.shiftKey });
  });

  document.querySelector("#propertyForm").addEventListener("change", handlePropertyChange);
  textFont.addEventListener("change", changeTextFont);
  elements.processCut.addEventListener("change", () => elements.processCut.checked && setSelectedProcess("cut"));
  elements.processEngrave.addEventListener("change", () => elements.processEngrave.checked && setSelectedProcess("engrave"));
  elements.projectName.addEventListener("change", () => {
    const value = elements.projectName.value.trim();
    if (!value) {
      elements.projectName.value = state.name;
      announce("Projektnavnet må ikke være tomt.");
    } else if (value !== state.name) {
      commit("Projektnavnet er ændret.", () => { state.name = value; });
    }
  });
  elements.projectName.addEventListener("keydown", (event) => { if (event.key === "Enter") elements.projectName.blur(); });

  document.querySelector("#rotateLeftButton").addEventListener("click", () => rotateSelected(-90));
  document.querySelector("#rotateRightButton").addEventListener("click", () => rotateSelected(90));
  document.querySelector("#resetRotationButton").addEventListener("click", () => {
    const objects = selectedObjects();
    if (!objects.length || objects.every((object) => !object.rotation)) return;
    commit("De valgte dele står lige igen.", () => { objects.forEach((object) => { object.rotation = 0; }); });
  });
  document.querySelector("#centerObjectButton").addEventListener("click", () => {
    const objects = selectedObjects();
    const bounds = unionBounds(objects);
    if (!bounds) return;
    const deltaX = state.workspace.widthMm / 2 - bounds.centerX;
    const deltaY = state.workspace.heightMm / 2 - bounds.centerY;
    moveSelected(deltaX, deltaY, objects.length === 1 ? `${objects[0].name} er centreret.` : `${objects.length} dele er centreret.`);
  });
  elements.alignReference.addEventListener("change", () => {
    elements.geometryMessage.classList.remove("error");
    renderControls();
  });
  document.querySelectorAll("[data-align]").forEach((button) => button.addEventListener("click", () => alignSelected(button.dataset.align)));
  document.querySelectorAll("[data-boolean]").forEach((button) => button.addEventListener("click", () => performGeometry(button.dataset.boolean)));

  document.querySelector("#fitButton").addEventListener("click", fitDrawing);
  document.querySelector("#zoomInButton").addEventListener("click", () => stepZoom(1));
  document.querySelector("#zoomOutButton").addEventListener("click", () => stepZoom(-1));

  elements.reviewButton.addEventListener("click", openReview);
  document.querySelector("#closeReviewButton").addEventListener("click", () => closeDialog(elements.reviewDialog));
  elements.cancelReviewButton.addEventListener("click", () => closeDialog(elements.reviewDialog));
  elements.finishReviewButton.addEventListener("click", () => closeDialog(elements.reviewDialog));
  elements.submitTestButton.addEventListener("click", submitDrawing);
  ["#reviewStudentName", "#reviewClass"].forEach((selector) => {
    const field = document.querySelector(selector);
    field.addEventListener("input", () => { if (field.value.trim()) setFieldError(field, false); });
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); submitDrawing(); }
    });
  });
  // Keep the dialog open while a submission is in flight, so the pupil sees the answer.
  elements.reviewDialog.addEventListener("cancel", (event) => { if (submitting) event.preventDefault(); });

  document.querySelector("#newProjectButton").addEventListener("click", () => openDialog(elements.newProjectDialog, document.querySelector("#keepProjectButton")));
  document.querySelector("#keepProjectButton").addEventListener("click", () => closeDialog(elements.newProjectDialog));
  document.querySelector("#confirmNewProjectButton").addEventListener("click", () => {
    closeDialog(elements.newProjectDialog);
    try { sessionStorage.removeItem("laservaerksted.pupil"); } catch {}
    document.querySelector("#reviewStudentName").value = "";
    document.querySelector("#reviewClass").value = "";
    commit("En ny tom tegning er klar.", () => { state = createEmptyProject(); });
    requestAnimationFrame(fitDrawing);
  });

  document.querySelector("#helpButton").addEventListener("click", () => openDialog(elements.helpDialog, document.querySelector("#closeHelpButton")));
  document.querySelector("#closeHelpButton").addEventListener("click", () => closeDialog(elements.helpDialog));
  document.querySelector("#closeFileErrorButton").addEventListener("click", () => closeDialog(elements.fileErrorDialog));
  document.querySelector("#saveProjectButton").addEventListener("click", () => { if (!projectAdapter.inline.active) saveProject(); });
  document.querySelector("#openProjectButton").addEventListener("click", () => { if (!projectAdapter.inline.active) elements.fileInput.click(); });
  elements.fileInput.addEventListener("change", () => openProjectFile(elements.fileInput.files?.[0]));

  [elements.reviewDialog, elements.newProjectDialog, elements.helpDialog, elements.fileErrorDialog].forEach((dialog) => dialog.addEventListener("close", handleDialogClose));
  window.addEventListener("keydown", handleGlobalKeydown);
  document.addEventListener("copy", handleObjectCopy);
  document.addEventListener("cut", handleObjectCopy);
  document.addEventListener("paste", handleObjectPaste);
  window.addEventListener("blur", () => cancelInteraction("Handlingen blev afbrudt, da vinduet mistede fokus."));
  document.addEventListener("visibilitychange", () => { if (document.hidden) cancelInteraction("Handlingen blev afbrudt, da vinduet blev skjult."); });
  window.addEventListener("resize", () => {
    window.clearTimeout(window.__laserResizeTimer);
    window.__laserResizeTimer = window.setTimeout(() => {
      if (zoom < 90) fitDrawing();
      else renderSelectionOverlay();
    }, 180);
  });

  const starterSignature = JSON.stringify(createKeyringProject().objects);

  function replaceProject(nextProject) {
    cancelInteraction("");
    state = nextProject;
    history = [];
    future = [];
    lastSubmission = { signature: "", requestId: "" };
    normalizeSelection();
    renderAll();
    requestAnimationFrame(fitDrawing);
  }

  // Contract used by the device's shared project service (project-session.js).
  const projectAdapter = {
    type: "laser",
    extension: ".laser.json",
    inline: {
      active: false,
      save: document.querySelector("#saveProjectButton"),
      open: document.querySelector("#openProjectButton"),
      status: elements.statusMessage,
      actions: document.querySelector(".header-actions")
    },
    name: () => state.name,
    signature: () => JSON.stringify({ name: state.name, workspace: state.workspace, objects: state.objects }),
    hasContent: () => state.objects.length > 0 && JSON.stringify(state.objects) !== starterSignature,
    setName: (value) => {
      const name = String(value || "").trim().slice(0, 80);
      if (!name) return;
      if (projectFileBytes({ ...state, name }) > MAX_PROJECT_BYTES) throw new Error("Projektnavnet gør filen for stor. Vælg et kortere navn.");
      state.name = name;
      elements.projectName.value = name;
      renderAll();
    },
    prepareSave: () => {
      if (interactionState) cancelInteraction("");
    },
    export: async () => new Blob([JSON.stringify(projectPayload(), null, 2)], { type: "application/json" }),
    import: async (blob) => {
      if (!blob || typeof blob.text !== "function") throw new Error("Projektet kunne ikke læses.");
      if (blob.size > MAX_PROJECT_BYTES) throw new Error("Filen er for stor til Laserværkstedet.");
      let data;
      try {
        data = JSON.parse(await blob.text());
      } catch {
        throw new Error("Projektet kunne ikke læses. Det er måske gemt i den gamle lasereditor.");
      }
      replaceProject(validateProjectFile(data));
      return state;
    },
    reset: async () => {
      if (submitting) throw new Error("Vent, til afleveringen er færdig.");
      if (elements.reviewDialog.open) closeDialog(elements.reviewDialog);
      try { sessionStorage.removeItem("laservaerksted.pupil"); } catch {}
      document.querySelector("#reviewStudentName").value = "";
      document.querySelector("#reviewClass").value = "";
      replaceProject(createKeyringProject());
    }
  };
  window.ElevhubProjectAdapter = projectAdapter;

  window.LaserUsbSave?.setup({ getProject: projectPayload, getFilename: () => safeFilename(state.name), announce });
  normalizeSelection();
  renderAll();
  requestAnimationFrame(fitDrawing);
  if (window.opentype) textFonts.load()?.catch(() => {});
})();
