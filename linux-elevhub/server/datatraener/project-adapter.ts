import { get } from "svelte/store";
import {
  gestures,
  chosenGesture,
  model,
  trainingStatus,
  TrainingStatus,
  type GestureData
} from "./script/stores/mlStore";
import { state, outputting, message } from "./script/stores/uiStore";

const FORMAT = "elevhub-datatraener-project";
const VERSION = 1;
let projectName = "Datatræner-projekt";

function stable(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  return `{${Object.keys(value as object).sort().map(key => `${JSON.stringify(key)}:${stable((value as Record<string, unknown>)[key])}`).join(",")}}`;
}

function changed(): void {
  window.dispatchEvent(new Event("elevhub-project-changed"));
}

function assertSafeTransition(recordingOnly = false): void {
  const current = get(state);
  if (current.isRecording || (!recordingOnly && (current.isTraining || current.isFlashingDevice))) {
    throw new Error(current.isRecording
      ? "Stop optagelsen, før projektet gemmes eller skiftes."
      : "Vent, til Datatræneren er færdig, før projektet skiftes.");
  }
}

function validGestureData(value: unknown): value is GestureData[] {
  return Array.isArray(value) && value.every(item => item && typeof item === "object" &&
    typeof (item as GestureData).name === "string" && Number.isFinite((item as GestureData).ID) &&
    Array.isArray((item as GestureData).recordings));
}

(window as any).ElevhubProjectAdapter = {
  type: "datatraener",
  extension: ".json",
  name: () => projectName,
  setName: (value: string) => { projectName = String(value || "Datatræner-projekt").trim() || "Datatræner-projekt"; },
  signature: () => stable(get(gestures)),
  hasContent: () => get(gestures).length > 0,
  prepareSave: async () => { assertSafeTransition(true); },
  prepareFinish: async () => { assertSafeTransition(); },
  export: async () => {
    const project = {
      format: FORMAT,
      version: VERSION,
      type: "datatraener",
      name: projectName,
      gestureData: get(gestures),
      requiresRetraining: true,
      notice: "Datatræneren skal træne modellen igen efter import."
    };
    return new Blob([JSON.stringify(project)], { type: "application/json;charset=utf-8" });
  },
  import: async (blob: Blob, name?: string) => {
    assertSafeTransition();
    const project = JSON.parse(await blob.text());
    if (!project || project.format !== FORMAT || project.version !== VERSION || !validGestureData(project.gestureData)) {
      throw new Error("Filen er ikke et gyldigt Datatræner-projekt.");
    }
    gestures.set(project.gestureData);
    projectName = String(name || project.name || "Datatræner-projekt");
    chosenGesture.set(null);
    (model as any).set(undefined);
    trainingStatus.set(TrainingStatus.Untrained);
    changed();
  },
  reset: async () => {
    assertSafeTransition();
    gestures.set([]);
    projectName = "Datatræner-projekt";
    chosenGesture.set(null);
    (model as any).set(undefined);
    trainingStatus.set(TrainingStatus.Untrained);
    state.update(current => ({...current, isPredicting: false, isTesting: false, isRecording: false, isTraining: false, isFlashingDevice: false}));
    outputting.set({text: ""});
    message.set({warning: false, text: ""});
    changed();
  }
};
