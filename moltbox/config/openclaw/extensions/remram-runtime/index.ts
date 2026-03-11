import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { createSemanticRouterPlugin } from "./src/semantic-router.js";

export default function register(api: OpenClawPluginApi) {
  createSemanticRouterPlugin(api);
}
