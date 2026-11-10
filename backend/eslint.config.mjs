// Checks for the hand-written browser scripts. There is no build step: every file is served as it is, so a typo in a name or a
// key written twice is only found here (or by the learner). Errors fail CI; warnings are listed.
import globals from "globals";
export default [{
  files: ["**/*.js"],
  languageOptions: { ecmaVersion: 2023, sourceType: "script", globals: { ...globals.browser, ...globals.serviceworker,
    // what one script hands to the others (each file is its own <script>; these are the names they share)
    Data: "writable", Charts: "writable", ReviewVault: "writable", ReviewOpinion: "writable", module: "readonly", globalThis: "readonly" } },
  rules: { "no-undef": "error", "no-dupe-keys": "error", "no-dupe-args": "error", "no-unreachable": "error", "no-redeclare": ["error", { builtinGlobals: false }], "no-const-assign": "error",
           "no-func-assign": "error", "no-import-assign": "error", "no-obj-calls": "error", "use-isnan": "error", "valid-typeof": "error", "no-self-assign": "error",
           "no-unused-vars": ["warn", { args: "none" }] },
}];
