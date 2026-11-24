export type HeavyStatus = string

declare module 'claude-code' {
  interface PluginState {
    'heavy-pane': { status: Shaped<HeavyStatus> }
  }
}
