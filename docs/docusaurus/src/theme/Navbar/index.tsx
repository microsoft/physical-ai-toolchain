// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import Navbar from '@theme-original/Navbar';
import type { WrapperProps } from '@docusaurus/types';

type Props = WrapperProps<typeof Navbar>;

export default function NavbarWrapper(props: Props): React.ReactElement {
  React.useEffect(() => {
    const toggleSelector = 'button.navbar__toggle, button[aria-label="Toggle navigation bar"]';
    const sidebarSelector = '.navbar-sidebar';
    const focusableSelector = 'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const background = Array.from(document.querySelectorAll<HTMLElement>('main, footer'));
    let navigationOpen = false;

    const visibleSidebarControls = (): HTMLElement[] => {
      const sidebar = document.querySelector<HTMLElement>(sidebarSelector);
      if (!sidebar) return [];
      return Array.from(sidebar.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (element) => !element.closest('[inert]') && element.getClientRects().length > 0,
      );
    };

    const synchronizeNavigation = () => {
      const toggle = document.querySelector<HTMLElement>(toggleSelector);
      navigationOpen = toggle?.getAttribute('aria-expanded') === 'true';
      for (const element of background) {
        if (navigationOpen) element.setAttribute('inert', '');
        else element.removeAttribute('inert');
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (!navigationOpen) return;

      const toggle = document.querySelector<HTMLElement>(toggleSelector);
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        toggle?.click();
        window.requestAnimationFrame(() => toggle?.focus());
        return;
      }

      if (event.key !== 'Tab') return;
      const controls = visibleSidebarControls();
      if (controls.length === 0) return;

      const first = controls[0];
      const last = controls[controls.length - 1];
      const active = document.activeElement;
      if (!controls.includes(active as HTMLElement) || (!event.shiftKey && active === last)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      }
    };

    const observer = new MutationObserver(synchronizeNavigation);
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['aria-expanded', 'class'],
      childList: true,
      subtree: true,
    });
    document.addEventListener('keydown', handleKeyDown, true);
    synchronizeNavigation();

    return () => {
      observer.disconnect();
      document.removeEventListener('keydown', handleKeyDown, true);
      for (const element of background) element.removeAttribute('inert');
    };
  }, []);

  return (
    <header aria-label="Physical AI Toolchain documentation header">
      <Navbar {...props} />
    </header>
  );
}
