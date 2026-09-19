export function initializeResult(protocolVersion) {
  return {
    protocolVersion,
    hostInfo: { name: "Local strict-CSP smoke host", version: "1.0.0" },
    hostCapabilities: { serverTools: {} },
    hostContext: { theme: "light", displayMode: "inline" },
  };
}

export function initialNotification(result) {
  return { jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result };
}
