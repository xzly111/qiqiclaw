import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

    if (path === '/api/orchestrate') {
      return {
        dry_run: true,
        mode: 'ensemble',
        ok: true,
        state: {
          candidates: [
            { model: 'gpt-4', status: 'completed', summary: 'cand A' },
            { model: 'claude-3', status: 'completed', summary: 'cand B' }
          ],
          final: '[orchestration dry-run ensemble of 2]: do the task',
          mode: 'ensemble',
          schema_version: 1,
          status: 'done',
          task: 'do the task'
        },
        workflow: {
          edges: [
            ['START', 'decide'],
            ['decide', 'execute'],
            ['execute', 'aggregate'],
            ['aggregate', 'END']
          ],
          nodes: ['decide', 'execute', 'aggregate']
        }
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
    cleanup()
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

  it('switches to orchestration mode and posts /api/orchestrate', async () => {
    const api = installDesktopApi()

    render(<LangGraphView onClose={vi.fn()} />)
    await screen.findByText('langgraph 1.2.4')

    // Switch to the multi-model orchestration tab.
    fireEvent.click(screen.getByRole('button', { name: '切换到多模型编排模式' }))

    // The orchestration-only fields appear.
    expect(screen.getByLabelText('模型集')).toBeTruthy()
    expect(screen.getByLabelText('角色分工')).toBeTruthy()

    // Provide ensemble models + role assignment.
    fireEvent.change(screen.getByLabelText('模型集'), {
      target: { value: 'gpt-4,openrouter:claude-3' }
    })
    fireEvent.change(screen.getByLabelText('角色分工'), {
      target: { value: 'execute=fast' }
    })

    fireEvent.click(screen.getByRole('button', { name: '运行工作流' }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith(
        expect.objectContaining({
          method: 'POST',
          path: '/api/orchestrate',
          body: expect.objectContaining({
            mode: 'ensemble',
            models: 'gpt-4,openrouter:claude-3',
            model_assignments: { execute: 'fast' },
            task: '规划一个三步 QiQiClaw 桌面端冒烟测试流程。'
          })
        })
      )
    })

    // Final answer + candidate cards render. (Text also appears in the JSON
    // state dump, so allow multiple matches.)
    expect((await screen.findAllByText(/ensemble of 2/)).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('候选结果（2）')).toBeTruthy()
    expect(screen.getAllByText('cand A').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('cand B').length).toBeGreaterThanOrEqual(1)
  })
})
