// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import type { WrapperProps } from '@docusaurus/types';
import SearchBar from '@theme-original/SearchBar';

type Props = WrapperProps<typeof SearchBar>;

const _ANNOUNCE_QUIET_MS = 400;
const _ANNOUNCE_WRITE_MS = 60;

export default function SearchBarWrapper(props: Props): React.ReactElement {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const statusRef = React.useRef<HTMLDivElement>(null);
  const instanceId = React.useId().replace(/:/g, '');
  const descriptionId = `search-shortcut-description-${instanceId}`;
  const footerId = `search-footer-option-${instanceId}`;

  React.useEffect(() => {
    const root = containerRef.current;
    const status = statusRef.current;
    if (!root || !status) {
      return undefined;
    }

    let currentInput: HTMLInputElement | null = null;
    let guardedFocusInput: HTMLInputElement | null = null;
    let guardedBlurInput: HTMLInputElement | null = null;
    let footerActiveClass: string | null = null;
    let announceQuietTimer = 0;
    let announceWriteTimer = 0;
    let lastInputAt = 0;
    let lastMessageKey = '';
    let lastQuery = '';
    let lastResultCount: number | null = null;
    let lastOpenState = false;
    let observer: MutationObserver | null = null;
    let runSync = (): void => undefined;

    const getInput = () => root.querySelector<HTMLInputElement>('input.navbar__search-input');
    const getListbox = () => root.querySelector<HTMLElement>('[role="listbox"]');
    const getFooterLink = (listbox: HTMLElement | null) =>
      listbox?.querySelector<HTMLAnchorElement>('[class*="hitFooter"] a') ?? null;
    const getResultOptions = (listbox: HTMLElement | null) =>
      Array.from(listbox?.querySelectorAll<HTMLElement>('[role="option"]') ?? []).filter(
        (option) => !option.closest('[class*="hitFooter"]'),
      );

    const clearStatus = () => {
      window.clearTimeout(announceQuietTimer);
      window.clearTimeout(announceWriteTimer);
      status.textContent = '';
      lastMessageKey = '';
    };

    const announce = (count: number, query: string) => {
      const message = `${count} result${count === 1 ? '' : 's'} for ${query}`;
      const messageKey = `${query}\u0000${message}`;
      window.clearTimeout(announceQuietTimer);
      window.clearTimeout(announceWriteTimer);

      const flush = () => {
        const quietFor = performance.now() - lastInputAt;
        if (quietFor < _ANNOUNCE_QUIET_MS) {
          announceQuietTimer = window.setTimeout(flush, _ANNOUNCE_QUIET_MS - quietFor);
          return;
        }
        if (messageKey === lastMessageKey) {
          return;
        }
        lastMessageKey = messageKey;
        announceWriteTimer = window.setTimeout(() => {
          status.textContent = message;
        }, _ANNOUNCE_WRITE_MS);
      };

      announceQuietTimer = window.setTimeout(flush, _ANNOUNCE_QUIET_MS);
    };

    const setActiveOption = (
      input: HTMLInputElement,
      activeOption: HTMLElement | null,
      listbox = getListbox(),
    ) => {
      const footerLink = getFooterLink(listbox);
      const options = [...getResultOptions(listbox), ...(footerLink ? [footerLink] : [])];
      for (const option of options) {
        option.setAttribute('aria-selected', option === activeOption ? 'true' : 'false');
      }
      if (activeOption?.id) {
        input.setAttribute('aria-activedescendant', activeOption.id);
      } else {
        input.removeAttribute('aria-activedescendant');
      }
    };

    const clearFooterHighlight = (footerLink: HTMLAnchorElement | null) => {
      footerLink?.classList.remove('search-footer-active');
      if (footerLink && footerActiveClass) {
        footerLink.classList.remove(footerActiveClass);
      }
    };

    const findCursor = (listbox: HTMLElement) => {
      const holder = listbox.querySelector<HTMLElement>('[class*="cursor"]');
      const className = holder
        ? Array.from(holder.classList).find((name) => /cursor/i.test(name)) ?? null
        : null;
      return { holder, className: className ?? footerActiveClass };
    };

    let lastUserGesture: Event | null = null;
    const rememberUserGesture = (event: Event) => {
      lastUserGesture = event;
    };
    const isUserGestureInFlight = () =>
      Boolean(lastUserGesture) && lastUserGesture?.eventPhase !== Event.NONE;

    const releaseFocusGuard = () => {
      if (guardedFocusInput) {
        Reflect.deleteProperty(guardedFocusInput, 'focus');
        guardedFocusInput = null;
      }
    };
    const applyFocusGuard = (input: HTMLInputElement) => {
      if (guardedFocusInput === input) {
        return;
      }
      releaseFocusGuard();
      input.focus = function guardedFocus(options?: FocusOptions): void {
        if (document.activeElement !== this && !isUserGestureInFlight()) {
          return;
        }
        HTMLElement.prototype.focus.call(this, options);
      };
      guardedFocusInput = input;
    };

    const releaseBlurGuard = () => {
      if (guardedBlurInput) {
        Reflect.deleteProperty(guardedBlurInput, 'blur');
        guardedBlurInput = null;
      }
    };
    const applyBlurGuard = (input: HTMLInputElement) => {
      if (guardedBlurInput === input) {
        return;
      }
      releaseBlurGuard();
      input.blur = function guardedBlur(): void {
        if (document.activeElement === this) {
          HTMLElement.prototype.blur.call(this);
        }
      };
      guardedBlurInput = input;
    };

    const collectFocusCandidates = (origin: HTMLElement, backwards: boolean) => {
      const selector = [
        'a[href]',
        'button:not([disabled])',
        'input:not([disabled])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
      ].join(',');
      const elements = Array.from(document.querySelectorAll<HTMLElement>(selector))
        .filter((element) => element.getClientRects().length > 0)
        .filter((element) => element.getAttribute('tabindex') !== '-1');
      const originIndex = elements.indexOf(origin);
      if (originIndex < 0) {
        return [];
      }
      return backwards
        ? elements.slice(0, originIndex).reverse()
        : elements.slice(originIndex + 1);
    };

    const moveFocus = (origin: HTMLElement, backwards: boolean) => {
      for (const candidate of collectFocusCandidates(origin, backwards)) {
        candidate.focus();
        if (document.activeElement === candidate) {
          return candidate;
        }
      }
      return null;
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      const input = getInput();
      if (!input) {
        return;
      }
      const listbox = getListbox();
      const footerLink = getFooterLink(listbox);

      if (event.key === 'Tab') {
        event.stopImmediatePropagation();
        clearFooterHighlight(footerLink);
        input.removeAttribute('aria-activedescendant');
        const origin = document.activeElement instanceof HTMLElement ? document.activeElement : input;
        if (moveFocus(origin, event.shiftKey)) {
          event.preventDefault();
        }
        return;
      }

      if (event.key === 'Escape') {
        clearFooterHighlight(footerLink);
        setActiveOption(input, null, listbox);
        clearStatus();
        return;
      }

      if (!listbox || !footerLink) {
        return;
      }
      const options = getResultOptions(listbox);
      const lastOption = options.at(-1) ?? null;
      const activeId = input.getAttribute('aria-activedescendant');
      const isFooterActive = activeId === footerLink.id;
      const isLastActive = Boolean(lastOption) && activeId === lastOption?.id;

      if ((event.key === 'ArrowDown' || event.key === 'End') && lastOption && isLastActive) {
        event.preventDefault();
        event.stopImmediatePropagation();
        const { holder, className } = findCursor(listbox);
        if (holder && className) {
          footerActiveClass = className;
          holder.classList.remove(className);
        }
        footerLink.classList.add('search-footer-active');
        setActiveOption(input, footerLink, listbox);
        footerLink.scrollIntoView({ block: 'nearest' });
        return;
      }

      if (event.key === 'ArrowUp' && isFooterActive && lastOption) {
        event.preventDefault();
        event.stopImmediatePropagation();
        clearFooterHighlight(footerLink);
        if (footerActiveClass) {
          lastOption.classList.add(footerActiveClass);
        }
        setActiveOption(input, lastOption, listbox);
        lastOption.scrollIntoView({ block: 'nearest' });
        return;
      }

      if (event.key === 'ArrowDown' && isFooterActive) {
        clearFooterHighlight(footerLink);
        return;
      }

      if (event.key === 'Enter' && isFooterActive) {
        event.preventDefault();
        event.stopImmediatePropagation();
        footerLink.click();
      }
    };

    const detachInput = () => {
      currentInput?.removeEventListener('keydown', handleKeyDown, true);
      currentInput?.removeEventListener('input', noteInput);
      releaseFocusGuard();
      releaseBlurGuard();
      currentInput = null;
    };

    const noteInput = () => {
      lastInputAt = performance.now();
      runSync();
    };

    const sync = () => {
      const input = getInput();
      if (input !== currentInput) {
        detachInput();
        currentInput = input;
        currentInput?.addEventListener('keydown', handleKeyDown, { capture: true });
        currentInput?.addEventListener('input', noteInput);
        if (currentInput) {
          applyFocusGuard(currentInput);
          applyBlurGuard(currentInput);
        }
      }
      if (!input) {
        return;
      }

      input.removeAttribute('aria-labelledby');
      input.setAttribute('aria-label', 'Search documentation');
      const describedBy = new Set((input.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean));
      describedBy.add(descriptionId);
      input.setAttribute('aria-describedby', Array.from(describedBy).join(' '));

      const owns = input.getAttribute('aria-owns');
      if (owns) {
        input.setAttribute('aria-controls', owns);
      } else {
        input.removeAttribute('aria-controls');
      }

      const listbox = getListbox();
      const query = input.value.trim();
      const upstreamAttached = input.hasAttribute('aria-autocomplete');
      const upstreamExpanded = input.getAttribute('aria-expanded') === 'true';
      const listboxVisible = Boolean(listbox) &&
        (upstreamExpanded || (listbox?.getClientRects().length ?? 0) > 0);
      const isOpen = upstreamAttached && listboxVisible && query.length > 0;

      if (upstreamAttached) {
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      } else {
        input.removeAttribute('role');
        input.removeAttribute('aria-expanded');
      }

      const footerLink = getFooterLink(listbox);
      if (footerLink) {
        footerLink.id ||= footerId;
        footerLink.setAttribute('role', 'option');
        footerLink.setAttribute('tabindex', '-1');
      }

      const resultOptions = getResultOptions(listbox);
      const allOptions = [...resultOptions, ...(footerLink ? [footerLink] : [])];
      allOptions.forEach((option, index) => {
        option.setAttribute('aria-posinset', String(index + 1));
        option.setAttribute('aria-setsize', String(allOptions.length));
      });

      const activeId = input.getAttribute('aria-activedescendant');
      const activeOption = activeId
        ? allOptions.find((option) => option.id === activeId) ?? null
        : null;
      setActiveOption(input, isOpen ? activeOption : null, listbox);

      const clearButton = root.querySelector<HTMLButtonElement>('button[type="reset"], button[class*="clear" i]');
      if (clearButton) {
        clearButton.setAttribute('aria-label', 'Clear search');
      }

      const resultCount = resultOptions.length;
      if (!query || !isOpen) {
        clearStatus();
      } else if (
        query !== lastQuery ||
        resultCount !== lastResultCount ||
        isOpen !== lastOpenState
      ) {
        announce(resultCount, query);
      }
      lastQuery = query;
      lastResultCount = resultCount;
      lastOpenState = isOpen;
    };

    const observerConfig: MutationObserverInit = {
      childList: true,
      subtree: true,
      attributes: true,
    };
    runSync = () => {
      observer?.disconnect();
      try {
        sync();
      } finally {
        observer?.observe(root, observerConfig);
      }
    };

    document.addEventListener('keydown', rememberUserGesture, true);
    document.addEventListener('pointerdown', rememberUserGesture, true);
    runSync();
    observer = new MutationObserver(runSync);
    observer.observe(root, observerConfig);

    return () => {
      detachInput();
      document.removeEventListener('keydown', rememberUserGesture, true);
      document.removeEventListener('pointerdown', rememberUserGesture, true);
      observer?.disconnect();
      clearStatus();
    };
  }, [descriptionId, footerId]);

  return (
    <div ref={containerRef} className="searchA11yWrapper">
      <div id={descriptionId} className="srOnly">
        Keyboard shortcut: Control plus K
      </div>
      <div ref={statusRef} className="srOnly" role="status" aria-live="polite" aria-atomic="true" />
      <SearchBar {...props} />
    </div>
  );
}