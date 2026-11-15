"use strict";
/* Page shell helpers shared by every page (the document itself never scrolls; see tokens.css):
   while the keyboard is up the page is exactly the visible part, so nothing hides behind it, and a field that
   gets focus is brought to the middle of the scrolling content area. */
(() => {
  // home-screen mode (iOS reports it as navigator.standalone): the bottom bar needs room for the Home indicator
  if (navigator.standalone || (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches))
    document.documentElement.classList.add("standalone");
  const vv = window.visualViewport;
  const isField = (node) => Boolean(node && node.matches && node.matches('input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="button"]), textarea, select'));
  const keyboardUp = () => Boolean(vv && window.innerHeight - vv.height > 120);
  let blurTimer = null;
  // While the keyboard is up (a field has focus, or the visible height shrank a lot) the bottom navigation and the other bars fixed at
  // the bottom are hidden: only the visible part is left for the field you type in. They return when the field loses focus.
  const sync = () => document.body.classList.toggle("keyboard-open", isField(document.activeElement) || keyboardUp());
  if (vv) {
    vv.addEventListener("resize", () => {
      const covered = window.innerHeight - vv.height > 150;
      document.documentElement.style.setProperty("--app-h", covered ? vv.height + "px" : "");
      if (!covered) window.scrollTo(0, 0);
      sync();
      if (isField(document.activeElement)) setTimeout(() => document.activeElement.scrollIntoView({ block: "center" }), 60);
    });
  }
  document.addEventListener("focusin", (event) => {
    const field = event.target;
    if (isField(field)) {
      clearTimeout(blurTimer);
      sync();
      if (field.type !== "checkbox" && field.type !== "radio") {
        setTimeout(() => field.scrollIntoView({ block: "center" }), 250);
        setTimeout(() => { if (document.activeElement === field) field.scrollIntoView({ block: "center" }); }, 600);       // once the keyboard has finished rising
      }
    }
  });
  document.addEventListener("focusout", () => {
    clearTimeout(blurTimer);
    blurTimer = setTimeout(sync, 120);
  });
  // ("shell-ready" is sent by training.js once the last script has hooked up its pages: see the end of that file)
})();
