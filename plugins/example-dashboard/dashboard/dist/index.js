(function () {
  "use strict";

  function ExampleDashboardPlugin() {
    const sdk = window.__HERMES_PLUGIN_SDK__;
    const React = sdk && sdk.React;
    if (!React) return null;
    return React.createElement(
      "div",
      { className: "flex min-w-0 w-full max-w-full flex-col gap-4" },
      React.createElement(
        "section",
        { className: "border border-border bg-background-base/50 p-4" },
        React.createElement("h2", { className: "text-sm font-medium" }, "Example dashboard plugin"),
        React.createElement(
          "p",
          { className: "mt-2 text-sm text-muted-foreground normal-case tracking-normal" },
          "This bundled plugin provides a stable dashboard API route for test coverage."
        )
      )
    );
  }

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("example", ExampleDashboardPlugin);
  }
})();
