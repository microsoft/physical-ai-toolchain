import React from 'react';
import { render, screen } from '@testing-library/react';
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
    const link = screen.getByText('Card Title');
    expect(link.closest('a')?.getAttribute('href')).toBe('/docs/page');
  });

  it('renders the icon', () => {
    render(<IconCard {...defaultProps} />);
    expect(screen.getByTestId('icon')).toBeDefined();
  });

  it('renders description when provided', () => {
    render(<IconCard {...defaultProps} description="Details here" />);
    expect(screen.getByText('Details here')).toBeDefined();
  });

  it('does not render description when omitted', () => {
    const { container } = render(<IconCard {...defaultProps} />);
    expect(container.querySelector('p')).toBeNull();
  });
});
