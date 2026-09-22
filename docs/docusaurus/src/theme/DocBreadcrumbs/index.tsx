// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import Link from '@docusaurus/Link';
import { useSidebarBreadcrumbs } from '@docusaurus/plugin-content-docs/client';
import { translate } from '@docusaurus/Translate';
import { ThemeClassNames } from '@docusaurus/theme-common';
import { useHomePageRoute } from '@docusaurus/theme-common/internal';
import HomeBreadcrumbItem from '@theme-original/DocBreadcrumbs/Items/Home';
import DocBreadcrumbsStructuredData from '@theme-original/DocBreadcrumbs/StructuredData';
import clsx from 'clsx';

interface BreadcrumbLinkProps {
  children: React.ReactNode;
  href?: string;
  isLast: boolean;
}

function BreadcrumbLink({ children, href, isLast }: BreadcrumbLinkProps): React.ReactElement {
  if (isLast) {
    return (
      <span className="breadcrumbs__link" aria-current="page">
        {children}
      </span>
    );
  }

  return href ? (
    <Link className="breadcrumbs__link" href={href}>
      {children}
    </Link>
  ) : (
    <span className="breadcrumbs__link">{children}</span>
  );
}

export default function DocBreadcrumbs(): React.ReactElement | null {
  const breadcrumbs = useSidebarBreadcrumbs();
  const homePageRoute = useHomePageRoute();
  if (!breadcrumbs) {
    return null;
  }

  return (
    <>
      <DocBreadcrumbsStructuredData breadcrumbs={breadcrumbs} />
      <nav
        className={clsx(ThemeClassNames.docs.docBreadcrumbs, 'docBreadcrumbs')}
        aria-label={translate({
          id: 'theme.docs.breadcrumbs.navAriaLabel',
          message: 'Breadcrumbs',
          description: 'The ARIA label for the breadcrumbs',
        })}
      >
        <ul className="breadcrumbs">
          {homePageRoute && <HomeBreadcrumbItem />}
          {breadcrumbs.map((item, index) => {
            const isLast = index === breadcrumbs.length - 1;
            const href = item.type === 'category' && item.linkUnlisted ? undefined : item.href;
            return (
              <li
                key={`${item.label}-${index}`}
                className={clsx('breadcrumbs__item', { 'breadcrumbs__item--active': isLast })}
              >
                <BreadcrumbLink href={href} isLast={isLast}>
                  {item.label}
                </BreadcrumbLink>
              </li>
            );
          })}
        </ul>
      </nav>
    </>
  );
}