const STUB_URLS = {
  "@hermes/plugin-sdk": "file:///home/tony/projects/hermes-desktop-kit/time-machine/tests/.stubs/sdk.mjs",
  "react": "file:///home/tony/projects/hermes-desktop-kit/time-machine/tests/.stubs/react.mjs",
  "react/jsx-runtime": "file:///home/tony/projects/hermes-desktop-kit/time-machine/tests/.stubs/jsx-runtime.mjs"
}

export function resolve(specifier, context, nextResolve) {
  if (STUB_URLS[specifier]) {
    return { url: STUB_URLS[specifier], shortCircuit: true }
  }
  return nextResolve(specifier, context)
}
