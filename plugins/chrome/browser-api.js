export function browserAPI(scope = globalThis) {
  if (scope.chrome?.runtime && scope.chrome?.tabs && scope.chrome?.contextMenus) return scope.chrome;
  const source = scope.browser;
  if (!source?.runtime || !source?.tabs || !source?.contextMenus)
    throw new Error('Required WebExtension APIs are unavailable');
  return {
    sidePanel: source.sidePanel,
    sidebarAction: source.sidebarAction,
    runtime: {
      id: source.runtime.id,
      lastError: null,
      onInstalled: source.runtime.onInstalled,
      onMessage: source.runtime.onMessage,
      sendMessage(message, callback) {
        const pending = source.runtime.sendMessage(message);
        if (callback) pending.then(callback, () => callback(undefined));
        return pending;
      },
    },
    contextMenus: {
      onClicked: source.contextMenus.onClicked,
      create: source.contextMenus.create.bind(source.contextMenus),
      removeAll(callback) {
        const pending = source.contextMenus.removeAll();
        if (callback) pending.then(callback, callback);
        return pending;
      },
    },
    tabs: {
      ...source.tabs,
      create(properties, callback) {
        const pending = source.tabs.create(properties);
        if (callback) pending.then(callback, () => callback(undefined));
        return pending;
      },
    },
  };
}
