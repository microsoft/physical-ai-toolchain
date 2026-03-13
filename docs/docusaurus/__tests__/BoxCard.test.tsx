import React from 'react';
import { render, screen } from '@testing-library/react';
import BoxCard from '../src/components/BoxCard';

describe('BoxCard', () => {
  const links = [
    { label: 'Guide', href: '/guide' },
    { label: 'API', href: '/api' },
  ];

  it('renders the title', () => {
    render(<BoxCard title="Test Card" links={links} />);
    expect(screen.getByText('Test Card')).toBeDefined();
  });

  it('renders links', () => {
    render(<BoxCard title="Test Card" links={links} />);
    expect(screen.getByText('Guide')).toBeDefined();
    expect(screen.getByText('API')).toBeDefined();
  });

  it('renders description when provided', () => {
    render(<BoxCard title="Test Card" links={links} description="A description" />);
    expect(screen.getByText('A description')).toBeDefined();
  });

  it('does not render description when omitted', () => {
    const { container } = render(<BoxCard title="Test Card" links={links} />);
    expect(container.querySelector('p')).toBeNull();
  });

  it('renders icon when provided', () => {
    render(<BoxCard title="Test Card" links={links} icon="/img/icons/test.svg" />);
    const img = screen.getByRole('presentation');
    expect(img.getAttribute('src')).toBe('/img/icons/test.svg');
  });
});
