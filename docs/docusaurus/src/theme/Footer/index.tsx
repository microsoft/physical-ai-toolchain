// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React, { useId, type ReactNode } from 'react';
import clsx from 'clsx';
import {
  isMultiColumnFooterLinks,
  useThemeConfig,
  type FooterColumnItem,
  type FooterLinkItem as FooterLinkItemConfig,
} from '@docusaurus/theme-common';
import FooterLinkItem from '@theme-original/Footer/LinkItem';
import FooterLinks from '@theme-original/Footer/Links';
import FooterLogo from '@theme-original/Footer/Logo';
import FooterCopyright from '@theme-original/Footer/Copyright';
import FooterLayout from '@theme-original/Footer/Layout';

function ColumnLinkItem({ item }: { readonly item: FooterLinkItemConfig }): ReactNode {
  return item.html ? (
    <li
      className={clsx('footer__item', item.className)}
      dangerouslySetInnerHTML={{ __html: item.html }}
    />
  ) : (
    <li className="footer__item">
      <FooterLinkItem item={item} />
    </li>
  );
}

function FooterColumn({ column }: { readonly column: FooterColumnItem }): ReactNode {
  const headingId = useId();

  return (
    <div className={clsx('col footer__col', column.className)}>
      <h3 id={headingId} className="footer__title">
        {column.title}
      </h3>
      <ul className="footer__items clean-list" aria-labelledby={headingId}>
        {column.items.map((item, index) => (
          <ColumnLinkItem key={item.href ?? item.to ?? index} item={item} />
        ))}
      </ul>
    </div>
  );
}

function SemanticFooterLinks({ columns }: { readonly columns: FooterColumnItem[] }): ReactNode {
  return (
    <div className="row footer__links">
      {columns.map((column, index) => (
        <FooterColumn key={column.title ?? index} column={column} />
      ))}
    </div>
  );
}

function Footer(): ReactNode {
  const { footer } = useThemeConfig();
  if (!footer) {
    return null;
  }

  const { copyright, links, logo, style } = footer;
  const footerLinks = links && links.length > 0
    ? isMultiColumnFooterLinks(links)
      ? <SemanticFooterLinks columns={links} />
      : <FooterLinks links={links} />
    : null;

  return (
    <FooterLayout
      style={style}
      links={
        <>
          <h2 className="srOnly">Site footer</h2>
          {footerLinks}
        </>
      }
      logo={logo && <FooterLogo logo={logo} />}
      copyright={copyright && <FooterCopyright copyright={copyright} />}
    />
  );
}

export default React.memo(Footer);
