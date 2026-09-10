import { describe, expect, it } from 'vitest'

import type { OperatorStatus } from '@/api/operator'
import { selectNewerOperatorStatus } from '@/hooks/use-operator'

function status(serviceInstanceId: string, revision: number): OperatorStatus {
  return {
    serviceInstanceId,
    revision,
    state: 'running',
    sessionId: 'session-1',
    mode: 'teleoperate',
    workerPid: 42,
    lastCommand: null,
    cleanupUnconfirmed: false,
    error: null,
  }
}

describe('selectNewerOperatorStatus', () => {
  it('rejects duplicate and older revisions from the same service instance', () => {
    const current = status('service-1', 4)

    expect(selectNewerOperatorStatus(current, status('service-1', 3))).toBe(current)
    expect(selectNewerOperatorStatus(current, status('service-1', 4))).toBe(current)
  })

  it('accepts newer revisions and a new service instance snapshot', () => {
    expect(selectNewerOperatorStatus(status('service-1', 4), status('service-1', 5))).toEqual(
      status('service-1', 5),
    )
    expect(selectNewerOperatorStatus(status('service-1', 9), status('service-2', 0))).toEqual(
      status('service-2', 0),
    )
  })
})
