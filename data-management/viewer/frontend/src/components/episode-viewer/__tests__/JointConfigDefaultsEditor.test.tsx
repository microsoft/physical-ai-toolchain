import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import type { MockedFunction } from 'vitest'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { JointGroup } from '@/components/episode-viewer/joint-constants'
import { JointConfigDefaultsEditor } from '@/components/episode-viewer/JointConfigDefaultsEditor'

type OnSaveFn = (config: { groups: JointGroup[]; labels: Record<string, string> }) => void
type OnOpenChangeFn = (open: boolean) => void

interface RenderOptions {
  open?: boolean
  groups?: JointGroup[]
  labels?: Record<string, string>
  isSaving?: boolean
  saveError?: string
  onSave?: MockedFunction<OnSaveFn>
  onOpenChange?: MockedFunction<OnOpenChangeFn>
}

function renderEditor(options: RenderOptions = {}) {
  const onSave = options.onSave ?? vi.fn<OnSaveFn>()
  const onOpenChange = options.onOpenChange ?? vi.fn<OnOpenChangeFn>()
  const utils = render(
    <JointConfigDefaultsEditor
      open={options.open ?? true}
      onOpenChange={onOpenChange}
      groups={options.groups ?? [{ id: 'arm', label: 'Arm', indices: [0] }]}
      labels={options.labels ?? { '0': 'shoulder' }}
      onSave={onSave}
      isSaving={options.isSaving}
      saveError={options.saveError}
    />,
  )
  return { ...utils, onSave, onOpenChange }
}

describe('JointConfigDefaultsEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders dialog title when open and unmounts content when closed', () => {
    const { rerender } = renderEditor()
    expect(screen.getByText('Joint Configuration Defaults')).toBeInTheDocument()

    rerender(
      <JointConfigDefaultsEditor
        open={false}
        onOpenChange={vi.fn()}
        groups={[{ id: 'arm', label: 'Arm', indices: [0] }]}
        labels={{ '0': 'shoulder' }}
        onSave={vi.fn()}
      />,
    )
    expect(screen.queryByText('Joint Configuration Defaults')).not.toBeInTheDocument()
  })

  it('commits a new joint label on blur', async () => {
    const user = userEvent.setup()
    renderEditor()
    expect(screen.getByText('shoulder')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Edit joint label' }))
    const input = screen.getByDisplayValue('shoulder')
    await user.clear(input)
    await user.type(input, 'wrist')
    await user.tab()

    expect(screen.getByText('wrist')).toBeInTheDocument()
  })

  it('invokes onOpenChange(false) when Cancel is clicked', async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderEditor()

    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('invokes onSave with persisted config shape when Save is clicked', async () => {
    const user = userEvent.setup()
    const { onSave } = renderEditor()

    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith({
      groups: [{ id: 'arm', label: 'Arm', indices: [0] }],
      labels: { '0': 'shoulder' },
    })
  })

  it('keeps pending Save focusable and guarded while showing its saving state', async () => {
    const user = userEvent.setup()
    const { onSave } = renderEditor({ isSaving: true })
    const saveButton = screen.getByRole('button', { name: 'Saving…' })
    expect(saveButton).not.toBeDisabled()
    expect(saveButton).toHaveAttribute('aria-disabled', 'true')
    expect(saveButton).toHaveAttribute('aria-busy', 'true')
    saveButton.focus()
    await user.keyboard('{Enter}')
    expect(saveButton).toHaveFocus()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('announces a failed save within the open dialog and keeps the draft available', () => {
    renderEditor({ saveError: 'Settings changed elsewhere.' })

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Joint defaults were not saved. Settings changed elsewhere.',
    )
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('shoulder')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('retains the draft after a save conflict and restores opener focus on Escape', async () => {
    const user = userEvent.setup()
    const groups = [{ id: 'arm', label: 'Arm', indices: [0] }]
    const labels = { '0': 'shoulder' }
    const onSave = vi.fn<OnSaveFn>()
    function StatefulEditor() {
      const [open, setOpen] = useState(false)
      const [saveError, setSaveError] = useState<string>()
      return (
        <>
          <button onClick={() => setOpen(true)}>Edit defaults</button>
          <JointConfigDefaultsEditor
            open={open}
            onOpenChange={setOpen}
            groups={groups}
            labels={labels}
            onSave={(config) => {
              onSave(config)
              setSaveError('Settings changed elsewhere.')
            }}
            saveError={saveError}
          />
        </>
      )
    }
    render(<StatefulEditor />)
    const opener = screen.getByRole('button', { name: 'Edit defaults' })
    await user.click(opener)
    expect(screen.getByRole('button', { name: 'Edit group label' })).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Edit joint label' }))
    const input = screen.getByDisplayValue('shoulder')
    await user.clear(input)
    await user.type(input, 'wrist{Enter}')
    const saveButton = screen.getByRole('button', { name: 'Save' })
    await user.click(saveButton)
    expect(onSave).toHaveBeenCalledWith({ groups, labels: { '0': 'wrist' } })
    expect(screen.getByRole('alert')).toHaveTextContent('Settings changed elsewhere.')
    expect(screen.getByText('wrist')).toBeInTheDocument()
    expect(saveButton).toHaveFocus()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(opener).toHaveFocus())
  })

  it('shows duplicate-index alert and disables Save when indices collide', async () => {
    const user = userEvent.setup()
    const { onSave } = renderEditor({
      groups: [{ id: 'arm', label: 'Arm', indices: [0, 1] }],
      labels: { '0': 'shoulder', '1': 'elbow' },
    })

    const indexButtons = screen.getAllByRole('button', { name: 'Edit joint index' })
    await user.click(indexButtons[1])
    const indexInput = screen.getByDisplayValue('1')
    await user.clear(indexInput)
    await user.type(indexInput, '0{Enter}')

    expect(screen.getByText(/Fix duplicate indices before saving\./i)).toBeInTheDocument()
    const saveButton = screen.getByRole('button', { name: 'Save' })
    expect(saveButton).toBeDisabled()

    await user.click(saveButton)
    expect(onSave).not.toHaveBeenCalled()
  })
})
