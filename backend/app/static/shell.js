"use strict";
/* Page shell helpers shared by every page (the document itself never scrolls; see tokens.css):
   while the keyboard is up the page is exactly the visible part, so nothing hides behind it, and a field that
   gets focus is brought to the middle of the scrolling content area. */
(() => {
  // home-screen mode (iOS reports it as navigator.standalone): the bottom bar needs room for the Home indicator
  if (navigator.standalone || (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches))
    document.documentElement.classList.add("standalone");
  const vv = window.visualViewport;
  if (vv) {
    vv.addEventListener("resize", () => {
      const covered = window.innerHeight - vv.height > 150;
      document.documentElement.style.setProperty("--app-h", covered ? vv.height + "px" : "");
      if (!covered) window.scrollTo(0, 0);
    });
  }
  document.addEventListener("focusin", (event) => {
    const field = event.target;
    if (field.matches && field.matches("input, textarea, select") && field.type !== "checkbox" && field.type !== "radio")
      setTimeout(() => field.scrollIntoView({ block: "center" }), 250);
  });
  // every view script has loaded: the page the URL names (#/progress, #/model ...) can now draw itself
  window.dispatchEvent(new Event("shell-ready"));
})();
