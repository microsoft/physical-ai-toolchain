import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import IconCard from '../src/components/IconCard';

describe('IconCard', () => {
  const defaultProps = {
    icon: <svg data-testid="icon" />,
    supertitle: 'Category',
    title: 'Card Title',
    href: '/docs/page',
  };

  it('renders the supertitle', () => {
    render(<IconCard {...defaultProps} />);
    expect(screen.getByText('Category')).toBeDefined();
  });

  it('renders the title as a link', () => {
    render(<IconCard {...defaultProps} />);
    const article = screen.getByRole('article', { name: 'Card Title' });
    const heading = within(article).getByRole('heading', { level: 3, name: 'Card Title' });
    expect(within(heading).getByRole('link')).toHaveAttribute('href', '/docs/page');
    expect(within(article).getAllByRole('link')).toHaveLength(1);
    expect(within(article).queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders the icon', () => {
    render(<IconCard {...defaultProps} />);
    expect(screen.getByTestId('icon').parentElement).toHaveAttribute('aria-hidden', 'true');
  });

  it('renders description when provided', () => {
    render(<IconCard {...defaultProps} description="Details here" />);
    expect(screen.getByText('Details here')).toBeDefined();
  });

  it('does not render description when omitted', () => {
    const { container } = render(<IconCard {...defaultProps} />);
    expect(container.querySelector('p')).toBeNull();
  });

  it('has no detectable accessibility violations', async () => {
    const { container } = render(<IconCard {...defaultProps} description="Details here" />);
    expect(await axe(container)).toHaveNoViolations();
  });
});
