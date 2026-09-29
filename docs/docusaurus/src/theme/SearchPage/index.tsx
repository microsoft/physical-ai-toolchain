// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import type { WrapperProps } from '@docusaurus/types';
import SearchPage from '@theme-original/SearchPage';

type Props = WrapperProps<typeof SearchPage>;

export default function SearchPageWrapper(props: Props): React.ReactElement {
  const statusRef = React.useRef<HTMLDivElement>(null);
  const statusId = `search-results-status-${React.useId().replace(/:/g, '')}`;

  React.useEffect(() => {
    const status = statusRef.current;
    if (!status) {
      return undefined;
    }

    let syncTimer = 0;
    let announceTimer = 0;
    let zeroConfirmTimer = 0;
    let lastMessage = '';

    const getInput = () => document.querySelector<HTMLInputElement>('input[name="q"]');
    const initialInput = getInput();
    if (initialInput?.hasAttribute('autofocus')) {
      initialInput.removeAttribute('autofocus');
    }
    if (initialInput && document.activeElement === initialInput) {
      initialInput.blur();
      document.body.setAttribute('tabindex', '-1');
      document.body.focus();
      document.body.removeAttribute('tabindex');
    }

    const getResultsRoot = () =>
      document.querySelector<HTMLElement>('main, [role="main"]') ?? document.body;
    const getResultCount = () => getResultsRoot().querySelectorAll('article').length;
    const getResultSummary = () => {
      const container = getInput()?.closest('.container');
      return container?.querySelector(':scope > p') ?? null;
    };

    const announce = (message: string) => {
      if (message === lastMessage) {
        return;
      }
      lastMessage = message;
      window.clearTimeout(announceTimer);
      announceTimer = window.setTimeout(() => {
        status.textContent = message;
      }, 60);
    };

    const syncStatus = () => {
      const input = getInput();
      const query = input?.value.trim() ?? '';
      if (!query) {
        window.clearTimeout(announceTimer);
        window.clearTimeout(zeroConfirmTimer);
        status.textContent = '';
        lastMessage = '';
        return;
      }

      const count = getResultCount();
      if (count > 0) {
        window.clearTimeout(zeroConfirmTimer);
        announce(`${count} document${count === 1 ? '' : 's'} found`);
        return;
      }

      window.clearTimeout(zeroConfirmTimer);
      zeroConfirmTimer = window.setTimeout(() => {
        const currentQuery = getInput()?.value.trim() ?? '';
        if (currentQuery === query && getResultCount() === 0 && getResultSummary()) {
          announce('No documents found');
        }
      }, 1500);
    };

    const scheduleSync = () => {
      window.clearTimeout(syncTimer);
      syncTimer = window.setTimeout(syncStatus, 150);
    };

    initialInput?.addEventListener('input', scheduleSync);
    const observer = new MutationObserver((records) => {
      const relevant = records.some(
        (record) => record.target !== status && !status.contains(record.target),
      );
      if (relevant) {
        scheduleSync();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    scheduleSync();

    return () => {
      initialInput?.removeEventListener('input', scheduleSync);
      observer.disconnect();
      window.clearTimeout(syncTimer);
      window.clearTimeout(announceTimer);
      window.clearTimeout(zeroConfirmTimer);
    };
  }, []);

  return (
    <>
      <div
        ref={statusRef}
        id={statusId}
        className="srOnly"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      />
      <SearchPage {...props} />
    </>
  );
}