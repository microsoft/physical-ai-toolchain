import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExportPanel } from '@/components/curriculum/ExportPanel'

const baseProps = () => ({
  episodeCount: 100,
  onExport: vi.fn().mockResolvedValue(undefined),
})

describe('ExportPanel', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders the card title and default format description', () => {
    render(<ExportPanel {...baseProps()} />)

    expect(screen.getByText('Export Curriculum')).toBeInTheDocument()
    expect(screen.getByText('Efficient columnar format for training')).toBeInTheDocument()
  })

  it('initializes the filename with the current ISO date', () => {
    render(<ExportPanel {...baseProps()} />)

    const input = document.getElementById('filename') as HTMLInputElement
    expect(input).not.toBeNull()
    expect(input.value).toMatch(/^curriculum-\d{4}-\d{2}-\d{2}$/)
    expect(input.placeholder).toBe('curriculum-export')
  })

  it('updates the filename when the input changes', () => {
    render(<ExportPanel {...baseProps()} />)

    const input = document.getElementById('filename') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'my-export' } })

    expect(input.value).toBe('my-export')
  })

  it('initializes checkbox states with metadata, annotations, metrics on; anomalies off', () => {
    render(<ExportPanel {...baseProps()} />)

    const metadata = document.getElementById('include-metadata') as HTMLButtonElement
    const annotations = document.getElementById('include-annotations') as HTMLButtonElement
    const metrics = document.getElementById('include-metrics') as HTMLButtonElement
    const anomalies = document.getElementById('include-anomalies') as HTMLButtonElement

    expect(metadata.getAttribute('aria-checked')).toBe('true')
    expect(annotations.getAttribute('aria-checked')).toBe('true')
    expect(metrics.getAttribute('aria-checked')).toBe('true')
    expect(anomalies.getAttribute('aria-checked')).toBe('false')
  })

  it('toggles the metadata checkbox when clicked', () => {
    render(<ExportPanel {...baseProps()} />)

    const metadata = document.getElementById('include-metadata') as HTMLButtonElement
    expect(metadata.getAttribute('aria-checked')).toBe('true')

    fireEvent.click(metadata)
    expect(metadata.getAttribute('aria-checked')).toBe('false')

    fireEvent.click(metadata)
    expect(metadata.getAttribute('aria-checked')).toBe('true')
  })

  it('toggles the anomalies checkbox from off to on', () => {
    render(<ExportPanel {...baseProps()} />)

    const anomalies = document.getElementById('include-anomalies') as HTMLButtonElement
    expect(anomalies.getAttribute('aria-checked')).toBe('false')

    fireEvent.click(anomalies)
    expect(anomalies.getAttribute('aria-checked')).toBe('true')
  })

  it('renders the include-section labels for every option', () => {
    render(<ExportPanel {...baseProps()} />)

    expect(screen.getByText(/Episode metadata/i)).toBeInTheDocument()
    expect(screen.getByText(/Annotation data/i)).toBeInTheDocument()
    expect(screen.getByText(/Trajectory metrics/i)).toBeInTheDocument()
    expect(screen.getByText(/Detected anomalies/i)).toBeInTheDocument()
  })

  it('disables the export button when episodeCount is zero', () => {
    render(<ExportPanel {...baseProps()} episodeCount={0} />)

    const button = screen.getByRole('button', { name: /Export 0 Episodes/i })
    expect(button).toBeDisabled()
  })

  it('disables the export button when disabled prop is true', () => {
    render(<ExportPanel {...baseProps()} disabled />)

    const button = screen.getByRole('button', { name: /Export 100 Episodes/i })
    expect(button).toBeDisabled()
  })

  it('disables the export button and shows spinner text while exporting', () => {
    render(<ExportPanel {...baseProps()} isExporting />)

    const button = screen.getByRole('button', { name: /Exporting/i })
    expect(button).toBeDisabled()
    expect(screen.getByText('Exporting...')).toBeInTheDocument()
  })

  it('enables the export button when episodes are available and not exporting', () => {
    render(<ExportPanel {...baseProps()} episodeCount={50} />)

    const button = screen.getByRole('button', { name: /Export 50 Episodes/i })
    expect(button).not.toBeDisabled()
  })

  it('formats large episode counts using locale separators', () => {
    render(<ExportPanel {...baseProps()} episodeCount={1234567} />)

    expect(screen.getByText(/Export 1,234,567 Episodes/)).toBeInTheDocument()
  })

  it('invokes onExport with the current options when the button is clicked', () => {
    const onExport = vi.fn().mockResolvedValue(undefined)
    render(<ExportPanel {...baseProps()} onExport={onExport} episodeCount={42} />)

    const filename = document.getElementById('filename') as HTMLInputElement
    fireEvent.change(filename, { target: { value: 'curated-batch' } })

    const anomalies = document.getElementById('include-anomalies') as HTMLButtonElement
    fireEvent.click(anomalies)

    fireEvent.click(screen.getByRole('button', { name: /Export 42 Episodes/i }))

    expect(onExport).toHaveBeenCalledTimes(1)
    expect(onExport).toHaveBeenCalledWith({
      format: 'parquet',
      filename: 'curated-batch',
      includeMetadata: true,
      includeAnnotations: true,
      includeTrajectoryMetrics: true,
      includeAnomalies: true,
    })
  })

  it('passes through className to the root card element', () => {
    const { container } = render(<ExportPanel {...baseProps()} className="custom-export" />)

    expect(container.firstChild).toHaveClass('custom-export')
  })

  it('renders the format select trigger with parquet selected by default', () => {
    render(<ExportPanel {...baseProps()} />)

    const trigger = screen.getByRole('combobox')
    expect(trigger).toBeInTheDocument()
    expect(trigger.textContent).toMatch(/parquet/i)
  })
})
