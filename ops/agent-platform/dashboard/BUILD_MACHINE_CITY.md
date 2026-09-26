# Machine City build contract (#235 handoff)

The deployed bundle is generated from modular sources under
`agent_platform_dashboard/static/`. Do not hand-edit `machine-city.js`.

Pinned build toolchain:

- esbuild `0.28.1`
- three `0.180.0`

The build script refuses different versions.

## Build

```bash
ESBUILD_BIN=/path/to/esbuild \
THREE_ROOT=/path/to/node_modules/three \
./ops/agent-platform/dashboard/build-machine-city.sh
```

On `quantlab-staging-01` the currently verified toolchain is available under the
Hermes installation; those host paths are operational details and are not embedded
in the source contract.

## Tests

The browser contract tests do not need Three.js:

```bash
node --test ops/agent-platform/dashboard/agent_platform_dashboard/static/dashboard-ui.test.js
```

Geometry tests can be bundled with the same pinned esbuild/Three.js pair and run
with Node. The generated production bundle should be rebuilt after every source
change and reviewed together with the source modules.

## Source graph

`machine-city-source.js` mounts `dashboard-ui.js` and `machine-scene.js`.
`machine-scene.js` consumes `machine-core.js` and `machine-entities.js`.
The reactive core and all live animation state therefore have reviewable source;
the minified bundle is only a generated deploy artifact.