# Copyright (c) 2011-2020 Eric Froemling
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# -----------------------------------------------------------------------------
"""Defines a keep-away game type."""

# ba_meta require api 6
# (see https://ballistica.net/wiki/meta-tag-system)

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

import ba
from bastd.actor.playerspaz import PlayerSpaz
from bastd.actor.scoreboard import Scoreboard
from bastd.actor.flag import (Flag, FlagDroppedMessage, FlagDiedMessage,
                              FlagPickedUpMessage)

if TYPE_CHECKING:
    from typing import Any, Type, List, Dict, Optional, Sequence, Union


class FlagState(Enum):
    """States our single flag can be in."""
    NEW = 0
    UNCONTESTED = 1
    CONTESTED = 2
    HELD = 3


class Player(ba.Player['Team']):
    """Our player type for this game."""


class Team(ba.Team[Player]):
    """Our team type for this game."""


# ba_meta export game
class KeepAwayGame(ba.TeamGameActivity[Player, Team]):
    """Game where you try to keep the flag away from your enemies."""

    name = 'Keep Away'
    description = 'Carry the flag for a set length of time.'
    game_settings = [
        ('Hold Time', {
            'min_value': 10,
            'default': 30,
            'increment': 10
        }),
        ('Time Limit', {
            'choices': [('None', 0), ('1 Minute', 60), ('2 Minutes', 120),
                        ('5 Minutes', 300), ('10 Minutes', 600),
                        ('20 Minutes', 1200)],
            'default': 0
        }),
        ('Respawn Times', {
            'choices': [('Shorter', 0.25), ('Short', 0.5), ('Normal', 1.0),
                        ('Long', 2.0), ('Longer', 4.0)],
            'default': 1.0
        }),
    ]
    score_info = ba.ScoreInfo(label='Time Held')

    @classmethod
    def supports_session_type(cls, sessiontype: Type[ba.Session]) -> bool:
        return (issubclass(sessiontype, ba.DualTeamSession)
                or issubclass(sessiontype, ba.FreeForAllSession))

    @classmethod
    def get_supported_maps(cls, sessiontype: Type[ba.Session]) -> List[str]:
        return ba.getmaps('keep_away')

    def __init__(self, settings: Dict[str, Any]):
        super().__init__(settings)
        self._scoreboard = Scoreboard()
        self._swipsound = ba.getsound('swip')
        self._tick_sound = ba.getsound('tick')
        self._countdownsounds = {
            10: ba.getsound('announceTen'),
            9: ba.getsound('announceNine'),
            8: ba.getsound('announceEight'),
            7: ba.getsound('announceSeven'),
            6: ba.getsound('announceSix'),
            5: ba.getsound('announceFive'),
            4: ba.getsound('announceFour'),
            3: ba.getsound('announceThree'),
            2: ba.getsound('announceTwo'),
            1: ba.getsound('announceOne')
        }
        self._flag_spawn_pos: Optional[Sequence[float]] = None
        self._update_timer: Optional[ba.Timer] = None
        self._holding_players: List[Player] = []
        self._flag_state: Optional[FlagState] = None
        self._flag_light: Optional[ba.Node] = None
        self._scoring_team: Optional[Team] = None
        self._flag: Optional[Flag] = None

    def get_instance_description(self) -> Union[str, Sequence]:
        return ('Carry the flag for ${ARG1} seconds.',
                self.settings_raw['Hold Time'])

    def get_instance_description_short(self) -> Union[str, Sequence]:
        return ('carry the flag for ${ARG1} seconds',
                self.settings_raw['Hold Time'])

    def on_transition_in(self) -> None:
        self.default_music = ba.MusicType.KEEP_AWAY
        super().on_transition_in()

    def on_team_join(self, team: Team) -> None:
        team.gamedata['time_remaining'] = self.settings_raw['Hold Time']
        self._update_scoreboard()

    def on_begin(self) -> None:
        super().on_begin()
        self.setup_standard_time_limit(self.settings_raw['Time Limit'])
        self.setup_standard_powerup_drops()
        self._flag_spawn_pos = self.map.get_flag_position(None)
        self._spawn_flag()
        self._update_timer = ba.Timer(1.0, call=self._tick, repeat=True)
        self._update_flag_state()
        self.project_flag_stand(self._flag_spawn_pos)

    def _tick(self) -> None:
        self._update_flag_state()

        # Award points to all living players holding the flag.
        for player in self._holding_players:
            if player:
                assert self.stats
                self.stats.player_scored(player,
                                         3,
                                         screenmessage=False,
                                         display=False)

        scoring_team = self._scoring_team

        if scoring_team is not None:

            if scoring_team.gamedata['time_remaining'] > 0:
                ba.playsound(self._tick_sound)

            scoring_team.gamedata['time_remaining'] = max(
                0, scoring_team.gamedata['time_remaining'] - 1)
            self._update_scoreboard()
            if scoring_team.gamedata['time_remaining'] > 0:
                assert self._flag is not None
                self._flag.set_score_text(
                    str(scoring_team.gamedata['time_remaining']))

            # Announce numbers we have sounds for.
            try:
                ba.playsound(self._countdownsounds[
                    scoring_team.gamedata['time_remaining']])
            except Exception:
                pass

            # Winner.
            if scoring_team.gamedata['time_remaining'] <= 0:
                self.end_game()

    def end_game(self) -> None:
        results = ba.TeamGameResults()
        for team in self.teams:
            results.set_team_score(
                team, self.settings_raw['Hold Time'] -
                team.gamedata['time_remaining'])
        self.end(results=results, announce_delay=0)

    def _update_flag_state(self) -> None:
        for team in self.teams:
            team.gamedata['holding_flag'] = False
        self._holding_players = []
        for player in self.players:
            holding_flag = False
            try:
                assert isinstance(player.actor, (PlayerSpaz, type(None)))
                if (player.actor and player.actor.node
                        and player.actor.node.hold_node):
                    holding_flag = (
                        player.actor.node.hold_node.getnodetype() == 'flag')
            except Exception:
                ba.print_exception('exception checking hold flag')
            if holding_flag:
                self._holding_players.append(player)
                player.team.gamedata['holding_flag'] = True

        holding_teams = set(t for t in self.teams
                            if t.gamedata['holding_flag'])
        prev_state = self._flag_state
        assert self._flag is not None
        assert self._flag_light
        assert self._flag.node
        if len(holding_teams) > 1:
            self._flag_state = FlagState.CONTESTED
            self._scoring_team = None
            self._flag_light.color = (0.6, 0.6, 0.1)
            self._flag.node.color = (1.0, 1.0, 0.4)
        elif len(holding_teams) == 1:
            holding_team = list(holding_teams)[0]
            self._flag_state = FlagState.HELD
            self._scoring_team = holding_team
            self._flag_light.color = ba.normalized_color(holding_team.color)
            self._flag.node.color = holding_team.color
        else:
            self._flag_state = FlagState.UNCONTESTED
            self._scoring_team = None
            self._flag_light.color = (0.2, 0.2, 0.2)
            self._flag.node.color = (1, 1, 1)

        if self._flag_state != prev_state:
            ba.playsound(self._swipsound)

    def _spawn_flag(self) -> None:
        ba.playsound(self._swipsound)
        self._flash_flag_spawn()
        assert self._flag_spawn_pos is not None
        self._flag = Flag(dropped_timeout=20, position=self._flag_spawn_pos)
        self._flag_state = FlagState.NEW
        self._flag_light = ba.newnode('light',
                                      owner=self._flag.node,
                                      attrs={
                                          'intensity': 0.2,
                                          'radius': 0.3,
                                          'color': (0.2, 0.2, 0.2)
                                      })
        assert self._flag.node
        self._flag.node.connectattr('position', self._flag_light, 'position')
        self._update_flag_state()

    def _flash_flag_spawn(self) -> None:
        light = ba.newnode('light',
                           attrs={
                               'position': self._flag_spawn_pos,
                               'color': (1, 1, 1),
                               'radius': 0.3,
                               'height_attenuated': False
                           })
        ba.animate(light, 'intensity', {0.0: 0, 0.25: 0.5, 0.5: 0}, loop=True)
        ba.timer(1.0, light.delete)

    def _update_scoreboard(self) -> None:
        for team in self.teams:
            self._scoreboard.set_team_value(team,
                                            team.gamedata['time_remaining'],
                                            self.settings_raw['Hold Time'],
                                            countdown=True)

    def handlemessage(self, msg: Any) -> Any:
        if isinstance(msg, ba.PlayerDiedMessage):
            # Augment standard behavior.
            super().handlemessage(msg)
            self.respawn_player(msg.getplayer(Player))
        elif isinstance(msg, FlagDiedMessage):
            self._spawn_flag()
        elif isinstance(msg, (FlagDroppedMessage, FlagPickedUpMessage)):
            self._update_flag_state()
        else:
            super().handlemessage(msg)
