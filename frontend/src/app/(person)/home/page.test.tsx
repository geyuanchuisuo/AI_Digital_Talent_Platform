import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import PersonHomePage from './page';
import {setAuth} from '@/lib/auth';

it('provides a working entry to the personal profile', () => {
  setAuth('test', {id: 1, username: 'personal', role: 'personal', person_id: 129});
  render(<PersonHomePage />);
  expect(screen.getByRole('link', {name: /我的档案/}).getAttribute('href')).toBe('/my-profile');
});
