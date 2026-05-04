import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ProgressOverview } from '@/components/dashboard/ProgressOverview'

afterEach(() => {
  cleanup()
})

describe('ProgressOverview', () => {
  const baseProps = {
    totalEpisodes: 100,
    annotatedEpisodes: 25,
    pendingEpisodes: 75,
    episodesPerHour: 5,
  }

  it('renders the card title', () => {
    render(<ProgressOverview {...baseProps} />)
    expect(screen.getByText('Annotation Progress')).toBeInTheDocument()
  })

  it('computes and displays completion percent', () => {
    render(<ProgressOverview {...baseProps} />)
    expect(screen.getByText('25%')).toBeInTheDocument()
  })

  it('renders all four stat values with locale formatting', () => {
    render(
      <ProgressOverview
        totalEpisodes={1000}
        annotatedEpisodes={250}
        pendingEpisodes={750}
        episodesPerHour={5}
      />,
    )
    expect(screen.getByText('250')).toBeInTheDocument()
    expect(screen.getByText('750')).toBeInTheDocument()
    expect(screen.getByText('1,000')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
  })

  it('renders estimated hours remaining when episodesPerHour > 0', () => {
    render(<ProgressOverview {...baseProps} />)
    // ceil(75 / 5) = 15
    expect(screen.getByText(/Estimated time remaining/)).toBeInTheDocument()
    expect(screen.getByText(/15 hours/)).toBeInTheDocument()
  })

  it('omits estimated time block when episodesPerHour is 0', () => {
    render(
      <ProgressOverview
        totalEpisodes={100}
        annotatedEpisodes={25}
        pendingEpisodes={75}
        episodesPerHour={0}
      />,
    )
    expect(screen.queryByText(/Estimated time remaining/)).not.toBeInTheDocument()
  })

  it('uses singular "hour" when only one hour remains', () => {
    render(
      <ProgressOverview
        totalEpisodes={100}
        annotatedEpisodes={99}
        pendingEpisodes={1}
        episodesPerHour={5}
      />,
    )
    expect(screen.getByText(/1 hour\b/)).toBeInTheDocument()
  })

  it('handles zero total episodes without dividing by zero', () => {
    render(
      <ProgressOverview
        totalEpisodes={0}
        annotatedEpisodes={0}
        pendingEpisodes={0}
        episodesPerHour={0}
      />,
    )
    expect(screen.getByText('0%')).toBeInTheDocument()
  })
})
