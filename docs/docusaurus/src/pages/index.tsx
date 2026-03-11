import React from 'react';
import Layout from '@theme/Layout';
import HeroSection from '../components/HeroSection';
import CardGrid from '../components/CardGrid';
import IconCard from '../components/IconCard';
import BoxCard from '../components/BoxCard';
import { iconCards, boxCards } from '../data/hubCards';

export default function Home(): React.ReactElement {
  return (
    <Layout>
      <HeroSection
        title="Azure NVIDIA Robotics Reference Architecture"
        subtitle="End-to-end reference architecture for training, deploying, and operating autonomous robots with Azure and NVIDIA Isaac."
      />
      <main>
        <section style={{ padding: '2rem 1.5rem' }}>
          <h2>Explore the platform</h2>
          <CardGrid columns={3}>
            {iconCards.map((card) => (
              <IconCard key={card.href} {...card} />
            ))}
          </CardGrid>
        </section>
        <section style={{ padding: '2rem 1.5rem', background: 'var(--ms-learn-section-bg)' }}>
          <h2>Deep dive</h2>
          <CardGrid columns={4}>
            {boxCards.map((card) => (
              <BoxCard key={card.title} {...card} />
            ))}
          </CardGrid>
        </section>
      </main>
    </Layout>
  );
}
