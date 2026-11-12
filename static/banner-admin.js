(() => {
  try {
    const input = document.querySelector('#image[name="image"]');
    const previews = document.querySelector("[data-banner-previews]");
    const warning = document.querySelector("#image-warning");
    const fillInputs = [...document.querySelectorAll("[data-fit-mode]")];
    const focusInputs = [...document.querySelectorAll("[data-focus]")];
    const focusControl = document.querySelector("[data-focus-control]");
    const smartNote = document.querySelector("[data-smart-fit-note]");
    const boxes = previews ? [...previews.querySelectorAll("[data-banner-preview-box]")] : [];
    const previewPairs = boxes.map((box) => ({
      box,
      backdrop: box.querySelector("[data-preview-backdrop]"),
      main: box.querySelector("[data-preview-main]"),
    })).filter((pair) => pair.box && pair.backdrop && pair.main);
    const selected = (items, fallback) => items.find((item) => item.checked)?.value || fallback;
    const updatePreview = () => {
      const mode = selected(fillInputs, "fill");
      const focus = selected(focusInputs, "center");
      if (focusControl) focusControl.hidden = mode === "fit";
      previewPairs.forEach(({ box, backdrop, main }) => {
        box.classList.toggle("banner-fit", mode === "fit");
        main.classList.remove("banner-focus-top", "banner-focus-center", "banner-focus-bottom");
        main.classList.add(`banner-focus-${focus}`);
        backdrop.hidden = mode !== "fit";
      });
    };
    fillInputs.forEach((item) => item.addEventListener("change", updatePreview));
    focusInputs.forEach((item) => item.addEventListener("change", updatePreview));
    updatePreview();
    if (!input || !previews || !warning) return;

    let objectUrl = null;
    let selection = 0;
    const revokePrevious = () => {
      if (objectUrl && typeof URL !== "undefined" && URL.revokeObjectURL) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    };
    const clearPreview = () => {
      previews.hidden = true;
      previewPairs.forEach(({ backdrop, main }) => {
        backdrop.removeAttribute("src");
        main.removeAttribute("src");
      });
    };
    const showFile = (file, src, currentSelection) => {
      if (currentSelection !== selection) return;
      previewPairs.forEach(({ backdrop, main }) => { backdrop.src = src; main.src = src; });
      previews.hidden = false;
      const image = new Image();
      image.onload = () => {
        try {
          if (currentSelection !== selection) return;
          const ratio = image.width / image.height;
          const shouldFit = ratio < 2.2;
          fillInputs.forEach((item) => { item.checked = item.value === (shouldFit ? "fit" : "fill"); });
          if (smartNote) {
            smartNote.hidden = !shouldFit;
            smartNote.textContent = shouldFit
              ? "This image is not wide enough to fill the banner, so we selected Fit."
              : "This image is wide enough to fill the banner. We selected Fill.";
          }
          updatePreview();
          const currentMode = selected(fillInputs, "fill");
          const guidance = currentMode === "fit"
            ? "Fit selected: the whole image will show over a blurred background."
            : "Fill selected: the image will be cropped to the banner shape; keep important content centred.";
          const qualityNote = image.width < 1200 || ratio < 3 || ratio > 5
            ? " This image is narrow or far from 4:1, so its display may look soft or crop more than expected."
            : "";
          warning.textContent = `${image.width} x ${image.height} (${ratio.toFixed(2)}:1). ${guidance}${qualityNote}`;
        } catch (error) {
          console.error("Could not update the banner image preview.", error);
        }
      };
      image.onerror = () => {
        if (currentSelection === selection) {
          clearPreview();
          warning.textContent = "Please choose a JPG, PNG or WebP image.";
        }
      };
      image.src = src;
    };
    const isSupportedImage = (file) => {
      const allowedType = ["image/jpeg", "image/png", "image/webp"];
      const allowedName = /\.(jpe?g|png|webp)$/i.test(file.name || "");
      return allowedType.includes(file.type) || (!file.type && allowedName);
    };
    const readAsDataUrl = (file, currentSelection) => {
      const reader = new FileReader();
      reader.onload = () => showFile(file, reader.result, currentSelection);
      reader.onerror = () => {
        if (currentSelection === selection) {
          clearPreview();
          warning.textContent = "The image could not be read. Please choose another JPG, PNG or WebP image.";
        }
      };
      reader.readAsDataURL(file);
    };
    input.addEventListener("change", () => {
      try {
        const currentSelection = ++selection;
        const file = input.files && input.files[0];
        revokePrevious();
        if (!file) return;
        if (!isSupportedImage(file)) {
          clearPreview();
          warning.textContent = "Please choose a JPG, PNG or WebP image.";
          return;
        }
        if (typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
          try {
            objectUrl = URL.createObjectURL(file);
            showFile(file, objectUrl, currentSelection);
            return;
          } catch (error) {
            console.error("Could not create a temporary banner preview URL; using FileReader.", error);
          }
        }
        readAsDataUrl(file, currentSelection);
      } catch (error) {
        console.error("Could not preview the selected banner image.", error);
        clearPreview();
        warning.textContent = "The image preview failed. Please choose another JPG, PNG or WebP image.";
      }
    });
    window.addEventListener("pagehide", revokePrevious, { once: true });
  } catch (error) {
    console.error("Banner admin preview could not initialize.", error);
  }
})();
