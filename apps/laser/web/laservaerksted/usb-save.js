(() => {
  "use strict";
  window.LaserUsbSave = Object.freeze({
    setup({ getProject, getFilename, announce }) {
      const bridge = window.laserKioskUsb;
      if (bridge?.version !== 1 || typeof bridge.listDevices !== "function" || typeof bridge.saveProject !== "function") return;
      const button = document.querySelector("#saveUsbButton");
      const buttonLabel = button.querySelector("span");
      const feedback = document.querySelector("#usbFeedback");
      const dialog = document.querySelector("#usbDialog");
      const choices = document.querySelector("#usbDeviceChoices");
      const dialogHint = document.querySelector("#usbDialogHint");
      const confirm = document.querySelector("#confirmUsbSaveButton");
      const cancel = document.querySelector("#cancelUsbSaveButton");
      const fallbackFocus = document.querySelector("#saveProjectButton");
      let devices = [], busy = false, timer, disposed = false, refreshing = null, lostChoice = "";

      function message(text, kind = "info") {
        feedback.textContent = text;
        feedback.dataset.kind = kind;
        feedback.hidden = !text;
      }

      function showError(error) {
        const messages = {
          DISCONNECTED: "USB-stikket er taget ud. Sæt det i igen, og prøv at gemme. Din tegning er stadig åben.",
          READ_ONLY: "USB-stikket er skrivebeskyttet. Brug et andet stik. Din tegning er stadig åben.",
          NO_SPACE: "Der er ikke plads på USB-stikket. Brug et andet stik, eller frigør plads. Din tegning er stadig åben.",
          INVALID_PROJECT: "Projektet kunne ikke gemmes. Brug Gem projekt, og behold tegningen åben.",
          UNAVAILABLE: "USB-stikket kunne ikke læses. Sæt det i igen, og prøv at gemme. Din tegning er stadig åben."
        };
        message(messages[error?.code] || "Gemningen på USB kunne ikke bekræftes. Behold stikket i, og prøv igen. Din tegning er stadig åben.", "error");
      }

      function updateButton() {
        if (!devices.length && document.activeElement === button) fallbackFocus.focus();
        button.hidden = !devices.length;
        button.disabled = busy;
        button.setAttribute("aria-busy", String(busy));
        buttonLabel.textContent = busy ? "Gemmer…" : "Gem til USB";
        button.title = devices.length === 1 ? "Gem projektet på " + devices[0].label : "Gem projektet på et USB-stik";
      }

      function renderChoices(initial = false) {
        const selectedId = choices.querySelector("input:checked")?.value;
        const activeId = choices.contains(document.activeElement) ? document.activeElement.value : null;
        choices.replaceChildren();
        let selected = false;
        if (initial) lostChoice = "";
        const previousDevice = devices.find(d => d.id === selectedId);
        if (selectedId && (!previousDevice || !previousDevice.writable)) {
          lostChoice = previousDevice ? "Det valgte USB-stik er skrivebeskyttet. Vælg et andet." : "Det valgte USB-stik er taget ud. Vælg et andet.";
        }
        const preferred = devices.find(d => d.id === selectedId && d.writable) || (initial ? devices.find(d => d.writable) : null);
        for (const device of devices) {
          const label = document.createElement("label");
          label.className = "usb-device-choice";
          const input = document.createElement("input");
          input.type = "radio";
          input.name = "usbDevice";
          input.value = device.id;
          input.disabled = !device.writable;
          input.checked = preferred?.id === device.id;
          selected ||= input.checked;
          const text = document.createElement("span");
          const name = document.createElement("strong");
          name.textContent = device.label;
          const detail = document.createElement("small");
          detail.textContent = device.detail + (device.writable ? "" : " · Skrivebeskyttet");
          text.append(name, detail);
          label.append(input, text);
          choices.append(label);
        }
        confirm.disabled = !selected || busy;
        dialogHint.textContent = devices.length ? (lostChoice || "Vælg det USB-stik, du vil gemme projektet på.") : "USB-stikkene er taget ud. Sæt et stik i igen, eller vælg Annuller.";
        if (!selected && document.activeElement === confirm) cancel.focus();
        if (activeId) {
          const next = Array.from(choices.querySelectorAll("input")).find(i => i.value === activeId && !i.disabled);
          (next || cancel).focus();
        }
      }

      async function refresh() {
        if (refreshing) return refreshing;
        refreshing = (async () => {
          try {
            const result = await bridge.listDevices();
            if (!Array.isArray(result)) throw new Error("Invalid USB list");
            const ids = new Set();
            const next = result.slice(0, 20).filter(d => d && typeof d.id === "string" && d.id.length <= 160 && !ids.has(d.id) && ids.add(d.id)).map(d => ({
              id: d.id, label: String(d.label || "USB-stik").slice(0, 80),
              detail: String(d.detail || ("USB · " + d.id.slice(-4))).slice(0, 80), writable: d.writable !== false
            }));
            const changed = JSON.stringify(next) !== JSON.stringify(devices);
            const inserted = !devices.length && next.length;
            devices = next;
            updateButton();
            if (changed && dialog.open) renderChoices();
            if (inserted && !dialog.open && !busy) announce("USB-stik fundet. Du kan nu vælge Gem til USB.");
            return true;
          } catch (error) {
            const hadDevices = devices.length > 0;
            devices = [];
            updateButton();
            if (dialog.open) renderChoices();
            if (hadDevices && !busy) showError({ code: "UNAVAILABLE" });
            return false;
          } finally {
            refreshing = null;
          }
        })();
        return refreshing;
      }

      async function save(device) {
        if (busy) return;
        const restoreFocus = document.activeElement === button || document.activeElement === confirm;
        busy = true;
        updateButton();
        message("Gemmer projektet på “" + device.label + "”…", "progress");
        try {
          const project = JSON.parse(JSON.stringify(getProject()));
          const result = await bridge.saveProject({ deviceId: device.id, filename: getFilename(), project });
          if (!result || typeof result.filename !== "string" || typeof result.deviceLabel !== "string") throw new Error("Missing save receipt");
          message("“" + result.filename + "” er gemt på “" + result.deviceLabel + "”.", "success");
        } catch (error) {
          showError(error);
        } finally {
          busy = false;
          await refresh();
          updateButton();
          if (restoreFocus && document.activeElement === document.body && !document.querySelector("dialog[open]")) {
            (button.hidden ? fallbackFocus : button).focus();
          }
        }
      }

      button.addEventListener("click", async () => {
        if (busy) return;
        const previousSingleId = devices.length === 1 ? devices[0].id : null;
        const available = await refresh();
        if (!available || !devices.length) {
          showError({ code: available ? "DISCONNECTED" : "UNAVAILABLE" });
          return;
        }
        if (previousSingleId && !devices.some(d => d.id === previousSingleId)) {
          showError({ code: "DISCONNECTED" });
          return;
        }
        if (devices.length === 1) {
          await save(devices[0]);
        } else if (!dialog.open) {
          renderChoices(true);
          dialog.showModal();
          (choices.querySelector("input:checked") || cancel).focus();
        }
      });
      choices.addEventListener("change", () => {
        lostChoice = "";
        dialogHint.textContent = "Vælg det USB-stik, du vil gemme projektet på.";
        confirm.disabled = !choices.querySelector("input:checked:not(:disabled)") || busy;
      });
      confirm.addEventListener("click", () => {
        const id = choices.querySelector("input:checked")?.value;
        const device = devices.find(d => d.id === id);
        if (!device) { renderChoices(); return; }
        dialog.close();
        void save(device);
      });
      cancel.addEventListener("click", () => dialog.close());
      dialog.addEventListener("close", () => (button.hidden ? fallbackFocus : button).focus());

      async function poll() {
        await refresh();
        if (!disposed) timer = setTimeout(poll, 2000);
      }
      window.addEventListener("pagehide", () => { disposed = true; clearTimeout(timer); });
      window.addEventListener("pageshow", event => { if (event.persisted) { disposed = false; void poll(); } });
      void poll();
    }
  });
})();
