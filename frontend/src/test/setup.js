// jsdom setup for the component tests.
//
// Two shims are needed and neither is a workaround for a bug in our code:
//
// `ResizeObserver` does not exist in jsdom, and Recharts' ResponsiveContainer
// requires it to measure its parent. Without the shim every chart test fails on
// an undefined constructor before it can assert anything.
//
// jsdom also reports every element as 0x0, so ResponsiveContainer would resolve
// to a zero-size chart and render no SVG at all. The element prototype is given
// non-zero client dimensions so the charts render something to assert on. This is
// the standard accommodation for charting libraries under jsdom — real browsers
// supply both, which is why these tests carry the data-shaping assertions on the
// exported pure functions and use the rendered output only for smoke checks.

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = ResizeObserverStub
}

const CHART_WIDTH = 800
const CHART_HEIGHT = 300

Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
  configurable: true,
  get() {
    return CHART_WIDTH
  },
})

Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
  configurable: true,
  get() {
    return CHART_HEIGHT
  },
})

Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
  configurable: true,
  value() {
    return {
      width: CHART_WIDTH,
      height: CHART_HEIGHT,
      top: 0,
      left: 0,
      bottom: CHART_HEIGHT,
      right: CHART_WIDTH,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }
  },
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})
