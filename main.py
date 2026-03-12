import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import challonge
import asyncio
import json
from typing import Dict, Any, Tuple, Union, List, Optional
from enum import Enum

load_dotenv()

class MatchStatus(Enum):
    FOUND = "found"
    NOT_FOUND = "not_found" 
    COMPLETED = "completed"
    NOT_READY = "not_ready"

try:
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    MENTOR_ID = int(os.environ["MENTOR_ID"])
    ORGANISERS_ID = int(os.environ["ORGANISERS_ID"])
    SERVER_ID = int(os.environ["SERVER_ID"])
    CATEGORY_ID = int(os.environ["CATEGORY_ID"])
    WALNUTT_ID = int(os.environ["WALNUTT_ID"])
    ROLE_CHANNEL_ID = int(os.environ["ROLE_CHANNEL_ID"])
    ROLE_MESSAGE_ID = int(os.environ["ROLE_MESSAGE_ID"])
    MATCH_OUTCOME_CHANNEL_ID = int(os.environ["MATCH_OUTCOME_CHANNEL_ID"])
    
    SCHEDULE_CHANNEL_ID = int(os.environ["SCHEDULE_CHANNEL_ID"])
    RING_A_MESSAGE_ID = int(os.environ.get("RING_A_MESSAGE_ID", "0"))
    RING_B_MESSAGE_ID = int(os.environ.get("RING_B_MESSAGE_ID", "0"))
    RING_C_MESSAGE_ID = int(os.environ.get("RING_C_MESSAGE_ID", "0"))
    RING_D_MESSAGE_ID = int(os.environ.get("RING_D_MESSAGE_ID", "0"))
    
    CHALLONGE_USERNAME = os.environ["CHALLONGE_USERNAME"]
    CHALLONGE_API_KEY = os.environ["CHALLONGE_API_KEY"]
    
    OPENS_ID = os.environ["OPENS_ID"]
    STANDARD_ID = os.environ["STANDARD_ID"]
    
except KeyError as e:
    raise KeyError(f"Missing environment variable: {e}")

challonge.set_credentials(CHALLONGE_USERNAME, CHALLONGE_API_KEY)

ROLE_IDS = {
    "standard": 1359346498084540517,
    "open": 1359346284456181850,
}
COMBO_ID = 1359346548760252527

match_states: Dict[str, Dict[str, Any]] = {}
active_matches: Dict[str, int] = {}

# Schedule system
schedule_data: Dict[str, List[str]] = {}
ring_message_ids = {
    "ring_a": RING_A_MESSAGE_ID,
    "ring_b": RING_B_MESSAGE_ID,
    "ring_c": RING_C_MESSAGE_ID,
    "ring_d": RING_D_MESSAGE_ID
}

intents = discord.Intents()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix='ram', intents=intents)

def load_schedule() -> bool:
    global schedule_data
    try:
        with open('schedule.json', 'r') as f:
            schedule_data = json.load(f)
        print(f"Loaded schedule: {len(schedule_data)} rings")
        return True
    except FileNotFoundError:
        print("schedule.json not found - schedule features disabled")
        return False
    except Exception as e:
        print(f"Error loading schedule: {e}")
        return False

class TournamentData:
    def __init__(self):
        self.matches = {}  # tournament_id -> matches
        self.participants = {}  # tournament_id -> participants
        self.last_updated = None
    
    async def refresh(self):
        try:
            standard_matches_task = asyncio.to_thread(challonge.matches.index, STANDARD_ID)
            opens_matches_task = asyncio.to_thread(challonge.matches.index, OPENS_ID)
            standard_participants_task = asyncio.to_thread(challonge.participants.index, STANDARD_ID)
            opens_participants_task = asyncio.to_thread(challonge.participants.index, OPENS_ID)
            
            standard_matches, opens_matches, standard_participants, opens_participants = await asyncio.gather(
                standard_matches_task, opens_matches_task, 
                standard_participants_task, opens_participants_task
            )
            
            self.matches[STANDARD_ID] = standard_matches
            self.matches[OPENS_ID] = opens_matches
            self.participants[STANDARD_ID] = standard_participants
            self.participants[OPENS_ID] = opens_participants
            self.last_updated = discord.utils.utcnow()
            
            print(f"Tournament data refreshed: {len(standard_matches)} standard matches, {len(opens_matches)} opens matches")
            return True
            
        except Exception as e:
            print(f"Error refreshing tournament data: {e}")
            return False
    
    def get_match_by_play_order(self, match_id: str):
        tournament_id = STANDARD_ID if match_id.startswith('S') else OPENS_ID
        match_number = int(match_id[1:])
        
        if tournament_id not in self.matches:
            return None
            
        for match in self.matches[tournament_id]:
            if match.get('suggested_play_order') == match_number:
                return match
        return None
    
    def get_participant_name(self, tournament_id: str, participant_id: int) -> str:
        if tournament_id not in self.participants or participant_id is None:
            return None
            
        for participant in self.participants[tournament_id]:
            if participant['id'] == participant_id:
                return participant['name']
        return None
    
    def get_match_status_fast(self, match_id: str):
        match = self.get_match_by_play_order(match_id)
        if not match:
            return MatchStatus.NOT_FOUND
            
        if match.get('state') == 'complete':
            return MatchStatus.COMPLETED
            
        if match['player1_id'] is None or match['player2_id'] is None:
            return MatchStatus.NOT_READY
            
        return MatchStatus.FOUND
    
    def get_match_teams_fast(self, match_id: str) -> Optional[Tuple[str, str]]:
        match = self.get_match_by_play_order(match_id)
        if not match:
            return None
            
        tournament_id = STANDARD_ID if match_id.startswith('S') else OPENS_ID
        
        team1_name = self.get_participant_name(tournament_id, match['player1_id'])
        team2_name = self.get_participant_name(tournament_id, match['player2_id'])
        
        if team1_name and team2_name:
            return (team1_name, team2_name)
        return None

# Global tournament data cache
tournament_cache = TournamentData()

async def get_match_teams(match_id: str) -> Optional[Tuple[str, str]]:
    if tournament_cache.last_updated:
        return tournament_cache.get_match_teams_fast(match_id)
    
    try:
        match_result = await get_match_details(match_id)
        if isinstance(match_result, tuple) and len(match_result) == 4:
            return (match_result[0], match_result[1])  # team1_name, team2_name
        return None
    except Exception:
        return None

async def find_current_match_in_ring_fast(ring_matches: List[str]) -> int:
    try:
        for i, match_id in enumerate(ring_matches):
            status = tournament_cache.get_match_status_fast(match_id)
            
            if status in [MatchStatus.NOT_FOUND, MatchStatus.NOT_READY, MatchStatus.COMPLETED]:
                continue
            else:
                return i
        
        return len(ring_matches)
    except Exception as e:
        print(f"Error finding current match: {e}")
        return 0

async def create_ring_embed_fast(ring_name: str, ring_matches: List[str]) -> discord.Embed:
    embed = discord.Embed(
        title=f"🏟️ {ring_name.upper().replace('_', ' ')} Schedule",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    
    current_pos = await find_current_match_in_ring_fast(ring_matches)
    
    if current_pos >= len(ring_matches):
        # All matches completed
        embed.add_field(
            name="🏁 All Matches Complete",
            value="No more matches scheduled for this ring.",
            inline=False
        )
        embed.color = discord.Color.green()
        return embed
    
    # Current match
    current_match = ring_matches[current_pos]
    teams = tournament_cache.get_match_teams_fast(current_match)
    if teams:
        team1, team2 = teams
        current_text = f"**{current_match}**: {team1} vs {team2}"
    else:
        current_text = f"**{current_match}**: Teams TBD"
    
    embed.add_field(
        name="🔴 CURRENT MATCH",
        value=current_text,
        inline=False
    )
    
    # Next 5 matches
    upcoming_matches = []
    for i in range(1, 6):  # Next 5 matches
        match_pos = current_pos + i
        if match_pos >= len(ring_matches):
            break
            
        match_id = ring_matches[match_pos]
        teams = tournament_cache.get_match_teams_fast(match_id)
        if teams:
            team1, team2 = teams
            upcoming_matches.append(f"**{match_id}**: {team1} vs {team2}")
        else:
            upcoming_matches.append(f"**{match_id}**: Teams TBD")
    
    if upcoming_matches:
        embed.add_field(
            name="📋 Upcoming Matches",
            value="\n".join(upcoming_matches),
            inline=False
        )
    else:
        embed.add_field(
            name="📋 Upcoming Matches",
            value="No more matches after current.",
            inline=False
        )
    
    return embed

async def update_all_ring_displays():
    if not schedule_data:
        return
        
    try:
        # Refresh tournament data once before updating all rings
        success = await tournament_cache.refresh()
        if not success:
            print("Failed to refresh tournament data, skipping schedule update")
            return
            
        channel = bot.get_channel(SCHEDULE_CHANNEL_ID)
        if not channel:
            return
            
        # Update all rings using cached data
        for ring_name, match_list in schedule_data.items():
            message_id = ring_message_ids.get(ring_name)
            if not message_id or message_id == 0:
                continue
                
            try:
                message = await channel.fetch_message(message_id)
                embed = await create_ring_embed_fast(ring_name, match_list)
                await message.edit(embed=embed)
            except discord.NotFound:
                print(f"Message not found for {ring_name}: {message_id}")
            except Exception as e:
                print(f"Error updating {ring_name} display: {e}")
                
    except Exception as e:
        print(f"Error updating ring displays: {e}")

async def create_ring_embed(ring_name: str, ring_matches: List[str]) -> discord.Embed:
    await tournament_cache.refresh()
    return await create_ring_embed_fast(ring_name, ring_matches)

class RoleMenu(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Standard Stream", style=discord.ButtonStyle.secondary,
                       custom_id="rolemenu:standard")
    async def role_one(self, interaction: discord.Interaction,
                       button: discord.ui.Button):
        await toggle_role(interaction, "standard")

    @discord.ui.button(label="Open Stream", style=discord.ButtonStyle.secondary,
                       custom_id="rolemenu:open")
    async def role_two(self, interaction: discord.Interaction,
                       button: discord.ui.Button):
        await toggle_role(interaction, "open")

async def toggle_role(interaction: discord.Interaction, key: str):
    role = interaction.guild.get_role(ROLE_IDS[key])
    member = interaction.user
    combo_role = interaction.guild.get_role(COMBO_ID)
    if combo_role is None:
        await send_error(interaction, "missing role")                         
        return

    if role is None:
        await send_error(interaction, "missing role")
        return

    if role in member.roles:
        await member.remove_roles(role)
        await interaction.response.send_message(f"➖ Removed **{role.name}**", ephemeral=True)
    else:
        await member.add_roles(role)
        await interaction.response.send_message(f"➕ Added **{role.name}**",  ephemeral=True)

    member = await interaction.guild.fetch_member(member.id)   

    roles = [interaction.guild.get_role(ROLE_IDS["open"]), interaction.guild.get_role(ROLE_IDS["standard"])]
    trig = any(r in member.roles for r in roles)
    if trig and combo_role not in member.roles:
        await member.add_roles(combo_role)
    elif not trig and combo_role in member.roles:
        await member.remove_roles(combo_role)

class MatchManagementView(discord.ui.View):
    def __init__(self, match_id: str, team1_name: str, team2_name: str, participant1_id: int, participant2_id: int, current_scores: tuple = (0, 0), managing_user_id: int = None):
        super().__init__(timeout=900)  # 5 minute timeout
        self.match_id = match_id.upper()  # Store in uppercase
        self.team1_name = team1_name
        self.team2_name = team2_name
        self.participant1_id = participant1_id
        self.participant2_id = participant2_id
        self.team1_score, self.team2_score = current_scores
        self.managing_user_id = managing_user_id
        
        # Store in global state
        match_states[self.match_id] = {
            'team1_name': team1_name,
            'team2_name': team2_name,
            'team1_score': self.team1_score,
            'team2_score': self.team2_score,
            'participant1_id': participant1_id,
            'participant2_id': participant2_id
        }
        
        # Mark this match as being actively managed
        if managing_user_id:
            active_matches[self.match_id] = managing_user_id

    async def on_timeout(self):
        # Clean up when view times out
        if self.match_id in active_matches:
            del active_matches[self.match_id]

    @discord.ui.button(label="Team 1", style=discord.ButtonStyle.primary, row=0)
    async def team1_point(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_admin_permission(interaction):
            return
        
        try:
            self.team1_score += 1
            match_states[self.match_id]['team1_score'] = self.team1_score
            
            if self.team1_score >= 2:  # Best of 3, need 2 wins
                await self._show_confirmation(interaction, self.team1_name)
            else:
                await self._update_match_display(interaction)
        except Exception as e:
            await send_error(interaction, f"Failed to update team 1 score: {e}")

    @discord.ui.button(label="Team 2", style=discord.ButtonStyle.primary, row=0)
    async def team2_point(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_admin_permission(interaction):
            return
        
        try:
            self.team2_score += 1
            match_states[self.match_id]['team2_score'] = self.team2_score
            
            if self.team2_score >= 2:  # Best of 3, need 2 wins
                await self._show_confirmation(interaction, self.team2_name)
            else:
                await self._update_match_display(interaction)
        except Exception as e:
            await send_error(interaction, f"Failed to update team 2 score: {e}")

    @discord.ui.button(label="Reset Scores", style=discord.ButtonStyle.secondary, row=1)
    async def reset_scores(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_admin_permission(interaction):
            return
        
        try:
            self.team1_score = 0
            self.team2_score = 0
            match_states[self.match_id]['team1_score'] = 0
            match_states[self.match_id]['team2_score'] = 0
            await self._update_match_display(interaction)
        except Exception as e:
            await send_error(interaction, f"Failed to reset scores: {e}")

    async def _check_admin_permission(self, interaction: discord.Interaction) -> bool:
        if not any(r.id == ORGANISERS_ID for r in interaction.user.roles):
            await interaction.response.send_message("You need the **Organisers** role to manage matches!", ephemeral=True)
            return False
        return True

    async def _update_match_display(self, interaction: discord.Interaction):
        embed = self._create_match_embed()
        # Update button labels with team names
        self.children[0].label = f"{self.team1_name}"
        self.children[1].label = f"{self.team2_name}"
        await interaction.response.edit_message(embed=embed, view=self)

    async def _show_confirmation(self, interaction: discord.Interaction, winning_team: str):
        embed = self._create_match_embed()
        embed.add_field(name="🏆 Match Complete!", value=f"**{winning_team}** wins!", inline=False)
        
        confirmation_view = MatchConfirmationView(
            self.match_id, 
            winning_team, 
            self.team1_name, 
            self.team2_name,
            self.participant1_id,
            self.participant2_id,
            self.team1_score,
            self.team2_score,
            self.managing_user_id
        )
        await interaction.response.edit_message(embed=embed, view=confirmation_view)

    def _create_match_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"Match Management: {self.match_id}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Teams", value=f"{self.team1_name} vs {self.team2_name}", inline=False)
        embed.add_field(name="Current Score", value=f"{self.team1_name}: {self.team1_score}\n{self.team2_name}: {self.team2_score}", inline=False)
        embed.add_field(name="Format", value="Best of 3", inline=False)
        return embed

class MatchConfirmationView(discord.ui.View):
    def __init__(self, match_id: str, winning_team: str, team1_name: str, team2_name: str, 
                 participant1_id: int, participant2_id: int, team1_score: int, team2_score: int,
                 managing_user_id: int = None):
        super().__init__(timeout=900)
        self.match_id = match_id.upper()
        self.winning_team = winning_team
        self.team1_name = team1_name
        self.team2_name = team2_name
        self.participant1_id = participant1_id
        self.participant2_id = participant2_id
        self.team1_score = team1_score
        self.team2_score = team2_score
        self.managing_user_id = managing_user_id

    async def on_timeout(self):
        # Clean up when view times out
        if self.match_id in active_matches:
            del active_matches[self.match_id]

    @discord.ui.button(label="Confirm Result", style=discord.ButtonStyle.success)
    async def confirm_result(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_admin_permission(interaction):
            return
        
        # Respond immediately to prevent timeout
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="⏳ Processing Result...",
                description="Submitting match result to Challonge, please wait...",
                color=discord.Color.orange()
            ),
            view=None
        )
            
        try:
            # Determine winner and submit to Challonge
            tournament_id = STANDARD_ID if self.match_id.startswith('S') else OPENS_ID
            match_number = int(self.match_id[1:])  # Remove S/O prefix
            
            # Get the actual match from Challonge using suggested_play_order
            matches = await asyncio.to_thread(challonge.matches.index, tournament_id)
            target_match = None
            
            for match in matches:
                if match.get('suggested_play_order') == match_number:
                    target_match = match
                    break
            
            if not target_match:
                await interaction.edit_original_response(
                    embed=discord.Embed(
                        title="❌ Error",
                        description="Could not find match in Challonge!",
                        color=discord.Color.red()
                    )
                )
                return
            
            # Determine winner ID and format scores correctly for Challonge
            # Get participant details to determine which team is which participant
            participants = await asyncio.to_thread(challonge.participants.index, tournament_id)
            participant1_name = next((p['name'] for p in participants if p['id'] == self.participant1_id), None)
            participant2_name = next((p['name'] for p in participants if p['id'] == self.participant2_id), None)
            
            # Determine which team corresponds to which participant and set scores accordingly
            if self.team1_name == participant1_name:
                # team1 = participant1, team2 = participant2
                participant1_score = self.team1_score
                participant2_score = self.team2_score
                winner_id = self.participant1_id if self.winning_team == self.team1_name else self.participant2_id
            elif self.team1_name == participant2_name:
                # team1 = participant2, team2 = participant1
                participant1_score = self.team2_score
                participant2_score = self.team1_score
                winner_id = self.participant2_id if self.winning_team == self.team1_name else self.participant1_id
            else:
                await interaction.edit_original_response(
                    embed=discord.Embed(
                        title="❌ Error",
                        description="Could not match teams to participants!",
                        color=discord.Color.red()
                    )
                )
                return
            
            scores_csv = f"{participant1_score}-{participant2_score}"
            
            # Update match in Challonge
            await asyncio.to_thread(
                challonge.matches.update,
                tournament_id,
                target_match['id'],
                scores_csv=scores_csv,
                winner_id=winner_id
            )
            
            # Send match outcome to channel
            try:
                channel = bot.get_channel(MATCH_OUTCOME_CHANNEL_ID)
                if channel:
                    outcome_embed = discord.Embed(
                        title="Match Result Confirmed",
                        color=discord.Color.green(),
                        timestamp=discord.utils.utcnow()
                    )
                    outcome_embed.add_field(name="Match ID", value=self.match_id, inline=True)
                    outcome_embed.add_field(name="Winner", value=f"🏆 **{self.winning_team}**", inline=True)
                    outcome_embed.add_field(name="Score", value=f"{self.team1_name}: {self.team1_score}\n{self.team2_name}: {self.team2_score}", inline=True)
                    outcome_embed.add_field(name="Players", value=f"{self.team1_name} vs {self.team2_name}", inline=False)
                    outcome_embed.add_field(name="Confirmed by", value=f"<@{interaction.user.id}>", inline=True)
                    outcome_embed.add_field(name="Submitted Score", value=scores_csv, inline=True)
                    
                    await channel.send(embed=outcome_embed)
            except Exception as e:
                print(f"Error sending to match outcome channel: {e}")
            
            # Clean up state
            if self.match_id in match_states:
                del match_states[self.match_id]
            if self.match_id in active_matches:
                del active_matches[self.match_id]
            
            # Update schedule displays after match confirmation
            try:
                await update_all_ring_displays()
            except Exception as e:
                print(f"Error updating schedule displays: {e}")
            
            # Update with success message
            embed = discord.Embed(
                title="✅ Match Result Confirmed",
                description=f"Match {self.match_id} has been completed and submitted to Challonge.",
                color=discord.Color.green()
            )
            embed.add_field(name="Winner", value=f"🏆 **{self.winning_team}**", inline=False)
            embed.add_field(name="Final Score", value=f"{self.team1_name}: {self.team1_score}\n{self.team2_name}: {self.team2_score}", inline=False)
            embed.add_field(name="Submitted to Challonge", value=f"Score: {scores_csv}", inline=False)
            
            await interaction.edit_original_response(embed=embed)
            
        except Exception as e:
            await send_error(interaction, f"Failed to submit match result: {e}")

    @discord.ui.button(label="Reset Match", style=discord.ButtonStyle.danger)
    async def reset_match(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_admin_permission(interaction):
            return
        
        try:
            # Reset scores and return to match management
            match_view = MatchManagementView(
                self.match_id, 
                self.team1_name, 
                self.team2_name, 
                self.participant1_id,
                self.participant2_id,
                (0, 0),
                self.managing_user_id
            )
            embed = match_view._create_match_embed()
            match_view.children[0].label = f"{self.team1_name}"
            match_view.children[1].label = f"{self.team2_name}"
            
            await interaction.response.edit_message(embed=embed, view=match_view)
        except Exception as e:
            await send_error(interaction, f"Failed to reset match: {e}")

    async def _check_admin_permission(self, interaction: discord.Interaction) -> bool:
        if not any(r.id == ORGANISERS_ID for r in interaction.user.roles):
            await interaction.response.send_message("You need the **Organisers** role to manage matches!", ephemeral=True)
            return False
        return True

async def get_match_details(match_id: str) -> Union[Tuple[str, str, int, int], MatchStatus]:
    try:
        tournament_id = STANDARD_ID if match_id.startswith('S') else OPENS_ID
        match_number = int(match_id[1:])  # Remove S/O prefix
        
        matches = await asyncio.to_thread(challonge.matches.index, tournament_id)
        target_match = None
        
        for match in matches:
            if match.get('suggested_play_order') == match_number:
                target_match = match
                break
        
        if not target_match:
            return MatchStatus.NOT_FOUND
            
        if target_match.get('state') == 'complete':
            return MatchStatus.COMPLETED
        
        participants = await asyncio.to_thread(challonge.participants.index, tournament_id)
        participant1_id = target_match['player1_id']
        participant2_id = target_match['player2_id']
        
        if participant1_id is None or participant2_id is None:
            return MatchStatus.NOT_READY
        
        participant1_name = next((p['name'] for p in participants if p['id'] == participant1_id), None)
        participant2_name = next((p['name'] for p in participants if p['id'] == participant2_id), None)
        
        if participant1_name is None or participant2_name is None:
            return MatchStatus.NOT_READY
        
        return (participant1_name, participant2_name, participant1_id, participant2_id)
        
    except Exception as e:
        print(f"Error getting match details: {e}")
        return MatchStatus.NOT_FOUND


@bot.event
async def on_ready():
    print("Bot starting")
    
    load_schedule()
    
    await bot.tree.sync()
    g = discord.Object(id=1359136696066769057)
    await bot.tree.sync(guild=g)
    print("Bot started")
    print(f"Logged in as {bot.user}")
    print([cmd.name for cmd in bot.tree.get_commands()])

@bot.event
async def setup_hook():
    bot.add_view(RoleMenu())

    channel = bot.get_channel(ROLE_CHANNEL_ID) or await bot.fetch_channel(ROLE_CHANNEL_ID)
    try:
        msg = await channel.fetch_message(ROLE_MESSAGE_ID)
        await msg.edit(view=RoleMenu())
        print("Role menu attached to existing message.")
    except discord.NotFound:
        print("Message ID invalid or deleted – use /send_rolemenu once to create a new one.")

@bot.tree.command(
    name="mentor-channel", 
    description="Create a new mentor channel",
)
async def mentor_channel(interaction: discord.Interaction, name: str):
    try:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not any(r.id == MENTOR_ID for r in interaction.user.roles):
            await interaction.response.send_message(f"You need the **Mentor** role to do this!", ephemeral=True)
            return
        
        category = interaction.guild.get_channel(CATEGORY_ID)
        if category is None or not isinstance(category, discord.channel.CategoryChannel):
            print(interaction.guild.categories)
            print(type(category))
            print(CATEGORY_ID)
            await send_error(interaction, "oopsie no category :(")
            return

        channel = await interaction.guild.create_text_channel(name = name, reason = f"Mentor channel created by {interaction.user}", category=category)

        await channel.set_permissions(interaction.user, view_channel = True, manage_channels = True, manage_permissions = True)

        await interaction.response.send_message(f"{channel.mention} successfully created!")
    except Exception as e:
        await send_error(interaction, e)

@bot.tree.command(
    name="mentor-add", 
    description="Add a member to the mentor channel",
)
async def mentor_add(interaction: discord.Interaction, user: discord.Member):
    if not any(r.id == MENTOR_ID for r in interaction.user.roles):
            await interaction.response.send_message(f"You need the **Mentor** role to do this!", ephemeral=True)
            return

    channel = interaction.channel
    if (channel.category_id != CATEGORY_ID):
        return await interaction.response.send_message("This command can only be run in mentor channels!", ephemeral=True)

    await channel.set_permissions(user, view_channel=True,send_messages=True,read_message_history=True)

    await interaction.response.send_message(f"{user.mention} successfully added to channel!")

@bot.tree.command(
    name="mentor-remove",
    description="Remove a member’s access to this mentor channel",
)
async def mentor_remove(interaction: discord.Interaction, user: discord.Member):
    if not any(r.id == MENTOR_ID for r in interaction.user.roles):
            await interaction.response.send_message(f"You need the **Mentor** role to do this!", ephemeral=True)
            return

    channel = interaction.channel
    if (channel.category_id != CATEGORY_ID):
        return await interaction.response.send_message("This command can only be run in mentor channels!", ephemeral=True)

    await channel.set_permissions(user, overwrite=None)

    await interaction.response.send_message(f"{user.mention} successfully removed from {channel.mention}.")


@bot.tree.command(
    name="send_rolemenu",
    description="Post the role-menu and tell you its message-ID.",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def slash_send_rolemenu(inter: discord.Interaction):
    view = RoleMenu()
    msg = await inter.channel.send(
        "## :exclamation: Click the buttons below to get your roles, depending on which stream you're in :exclamation: ",
        view=view,
    )
    await inter.response.send_message(
        f"env id: `{msg.id}`",
        ephemeral=True
    )

@bot.tree.command(
    name="match_manage",
    description="Manage a tournament match with live scoring"
)
async def match_manage(interaction: discord.Interaction, match_id: str):
    
    match_id = match_id.upper()
    
    if not any(r.id == ORGANISERS_ID for r in interaction.user.roles):
        await interaction.response.send_message("You need the **Organisers** role to manage matches!", ephemeral=True)
        return
    
    if match_id in active_matches:
        managing_user = active_matches[match_id]
        await interaction.response.send_message(f"This match is already being managed by <@{managing_user}>", ephemeral=True)
        return
    
    if not (match_id.startswith(('S', 'O')) and match_id[1:].isdigit()):
        await interaction.response.send_message("Invalid match ID format! Use format like S1, S34, O2, O14", ephemeral=True)
        return
    
    try:
        match_result = await get_match_details(match_id)
    except Exception as e:
        await send_error(interaction, f"Failed to fetch match details: {e}")
        return
    
    if match_result == MatchStatus.NOT_FOUND:
        await interaction.response.send_message("Match not found! Please check the match ID and try again.", ephemeral=True)
        return
    elif match_result == MatchStatus.COMPLETED:
        await interaction.response.send_message("This match has already been completed and cannot be managed.", ephemeral=True)
        return
    elif match_result == MatchStatus.NOT_READY:
        await interaction.response.send_message("This match is not ready yet! Complete the earlier matches first.", ephemeral=True)
        return
    
    team1_name, team2_name, participant1_id, participant2_id = match_result
    
    current_scores = (0, 0)
    if match_id in match_states:
        current_scores = (match_states[match_id]['team1_score'], match_states[match_id]['team2_score'])
    
    view = MatchManagementView(match_id, team1_name, team2_name, participant1_id, participant2_id, current_scores, interaction.user.id)
    embed = view._create_match_embed()
    
    view.children[0].label = f"{team1_name}"
    view.children[1].label = f"{team2_name}"
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(
    name="send_schedule",
    description="Create initial schedule messages for all rings",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def send_schedule(interaction: discord.Interaction):
    
    if not schedule_data:
        await interaction.response.send_message(
            "Schedule not loaded! Make sure schedule.json exists and restart the bot.",
            ephemeral=True
        )
        return
    
    try:
        channel = bot.get_channel(SCHEDULE_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message(
                "Schedule channel not found! Check SCHEDULE_CHANNEL_ID.",
                ephemeral=True
            )
            return
        
        message_ids = {}
        
        for ring_name in ["ring_a", "ring_b", "ring_c", "ring_d"]:
            if ring_name not in schedule_data:
                continue
                
            embed = await create_ring_embed(ring_name, schedule_data[ring_name])
            message = await channel.send(embed=embed)
            message_ids[ring_name] = message.id
        
        env_text = "\n".join([
            f"{ring.upper()}_MESSAGE_ID={msg_id}"
            for ring, msg_id in message_ids.items()
        ])
        
        await interaction.response.send_message(
            f"Schedule messages created! Add these to your .env file:\n\n```env\n{env_text}\n```",
            ephemeral=True
        )
        
    except Exception as e:
        await send_error(interaction, f"Failed to create schedule messages: {e}")

@bot.tree.command(
    name="update_schedule",
    description="Manually update all ring schedules"
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def update_schedule_command(interaction: discord.Interaction):
    
    if not schedule_data:
        await interaction.response.send_message(
            "schedule not loaded! make sure schedule.json exists",
            ephemeral=True
        )
        return
    
    await interaction.response.send_message("updating...", ephemeral=True)
    
    try:
        await update_all_ring_displays()
        await interaction.edit_original_response(content="schedule updated!")
    except Exception as e:
        await send_error(interaction, f"Failed to update schedules: {e}")

@bot.tree.command(
    name="reset_me",
    description="Clear all matches you are currently managing"
)
async def clear_my_matches(interaction: discord.Interaction):
    
    if not any(r.id == ORGANISERS_ID for r in interaction.user.roles):
        await interaction.response.send_message("You need the **Organisers** role to use this command!", ephemeral=True)
        return
    
    try:
        user_id = interaction.user.id
        
        managed_matches = [match_id for match_id, managing_user in active_matches.items() if managing_user == user_id]
        
        if not managed_matches:
            await interaction.response.send_message("You are not currently managing any matches.", ephemeral=True)
            return
        
        for match_id in managed_matches:
            if match_id in active_matches:
                del active_matches[match_id]
            if match_id in match_states:
                del match_states[match_id]
        
        match_list = ", ".join(managed_matches)
        await interaction.response.send_message(
            f"Cleared {len(managed_matches)} matches: {match_list}", 
            ephemeral=True
        )
        
    except Exception as e:
        await send_error(interaction, f"Failed to clear matches: {e}")


async def send_error(interaction: discord.Interaction, error):
    print(f"Error: {error}")
    error_message = f"There was a problem executing that command. Please ask <@{WALNUTT_ID}> for assistance: \n\"{error}\""
    
    if interaction.response.is_done():
        await interaction.edit_original_response(
            embed=discord.Embed(
                title="❌ Error",
                description=error_message,
                color=discord.Color.red()
            ),
            view=None
        )
    else:
        await interaction.response.send_message(error_message, ephemeral=True)

bot.run(BOT_TOKEN)