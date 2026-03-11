import React from 'react';
import Link from '@docusaurus/Link';
import useBaseUrl from '@docusaurus/useBaseUrl';
import styles from '../styles.module.css';

export interface BoxCardLink {
  label: string;
  href: string;
}

export interface BoxCardProps {
  title: string;
  description?: string;
  links: BoxCardLink[];
  icon?: string;
}

export default function BoxCard({ title, description, links, icon }: BoxCardProps): React.ReactElement {
  return (
    <div className={styles.boxCard}>
      {icon && (
        <div className={styles.boxCardIcon}>
          <img src={useBaseUrl(icon)} alt="" width="48" height="48" />
        </div>
      )}
      <h3 className={styles.boxCardTitle}>{title}</h3>
      {description && <p className={styles.cardDescription}>{description}</p>}
      <ul className={styles.boxCardLinks}>
        {links.map((link) => (
          <li key={link.href}>
            <Link to={link.href}>{link.label}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
