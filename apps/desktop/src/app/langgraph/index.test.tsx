import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LangGraphView } from './index'

const statusPayload = {
  available: true,
  capabilities: ['stategraph', 'state', 'nodes', 'edges', 'qiqiclaw-memory'],
  modes: ['dry-run', 'agent'],
  source_path: '/home/szd/下载/langgraph-main',
  version: '1.2.4',
  workflow: {
    edges: [
      ['START', 'qiqiclaw'],
      ['qiqiclaw', 'END']
    ],
    entrypoint: 'qiqiclaw_cli.langgraph_runner.build_qiqiclaw_graph',
    executor: 'qiqiclaw_cli.oneshot._run_agent',
    nodes: ['qiqiclaw']
  }
}

function installDesktopApi() {
  const api = vi.fn(async ({ path }: { path: string }) => {
    if (path === '/api/langgraph/status') {
      return statusPayload
    }

    if (path === '/api/langgraph/run') {
      return {
        dry_run: true,
        ok: true,
        state: {
          prompt: '规划一个三步 QiQiClaw 桌面端冒烟测试流程。',
          response: 'LangGraph dry-run routed prompt to QiQiClaw: smoke test complete',
          status: 'ok',
          toolsets: ['files', 'terminal']
        },
        workflow: statusPayload.workflow
      }
    }

    throw new Error(`unexpected path ${path}`)
  })

  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: { api }
  })

  return api
}

describe('LangGraphView', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    Reflect.deleteProperty(window, 'hermesDesktop')
  })

  it('loads LangGraph status and runs the dry-run workflow', async () => {
    const api = installDesktopApi()

    render(<LangGraphView onClose={vi.fn()} />)

    expect(await screen.findByText('langgraph 1.2.4')).toBeTruthy()
    expect(screen.getByText('START -> qiqiclaw qiqiclaw -> END')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '运行工作流' }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith(
        expect.objectContaining({
          method: 'POST',
          path: '/api/langgraph/run'
        })
      )
    })

    expect(await screen.findAllByText(/smoke test complete/)).toHaveLength(2)
    expect(screen.getByText(/"status": "ok"/)).toBeTruthy()
  })
})
