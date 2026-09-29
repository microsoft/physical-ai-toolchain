// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import { useHistory, useLocation } from '@docusaurus/router';
import type { WrapperProps } from '@docusaurus/types';
import Layout from '@theme-original/Layout';

type Props = WrapperProps<typeof Layout>;

let currentDocumentPathname: string | null = null;

export default function LayoutWrapper(props: Props): React.ReactElement {
  const history = useHistory();
  const { pathname, hash } = useLocation();

  React.useEffect(() => {
    const nameTablesOfContents = () => {
      document
        .querySelectorAll('.table-of-contents')
        .forEach((tableOfContents) => tableOfContents.setAttribute('aria-label', 'In this article'));
    };
    nameTablesOfContents();
    const observer = new MutationObserver(nameTablesOfContents);
    observer.observe(document.body, { childList: true, subtree: true });

    const fallback = document.getElementById('__docusaurus_skipToContent_fallback');
    const isSearchRoute = /(^|\/)search\/?$/.test(pathname);
    let promotedFallback = false;

    if (fallback && isSearchRoute && !document.querySelector('main')) {
      fallback.setAttribute('role', 'main');
      promotedFallback = true;
    }
    return () => {
      observer.disconnect();
      if (promotedFallback) {
        fallback?.removeAttribute('role');
      }
    };
  }, [pathname]);

  React.useEffect(() => {
    const priorPathname = currentDocumentPathname;
    currentDocumentPathname = pathname;

    if (priorPathname === null || priorPathname === pathname || history.action !== 'PUSH' || hash) {
      return;
    }

    const main = document.querySelector('main, [role="main"]');
    if (main instanceof HTMLElement) {
      main.setAttribute('tabindex', '-1');
      window.requestAnimationFrame(() => main.focus({ preventScroll: true }));
    }
  }, [hash, history.action, pathname]);

  return <Layout {...props} />;
}