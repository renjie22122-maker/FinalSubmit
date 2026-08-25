package org.twak.tweed.gen;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JPanel;

import org.twak.tweed.IDumpObjs;
import org.twak.tweed.Tweed;
import org.twak.tweed.handles.HandleMe;
import org.twak.utils.geom.ObjDump;
import org.twak.utils.geom.ObjRead;
import org.twak.utils.ui.ListDownLayout;

import com.jme3.material.Material;
import com.jme3.math.Vector3f;
import com.jme3.scene.Geometry;
import com.jme3.scene.Mesh;
import com.jme3.scene.VertexBuffer;
import com.jme3.util.BufferUtils;

/** Displays the optional RGB values stored after each OBJ vertex. */
public final class VertexColorObjGen extends Gen implements IDumpObjs {

	static final class ParsedObj {
		final float[] positions;
		final float[] colors;
		final float[] normals;
		final int[] indices;

		ParsedObj( float[] positions, float[] colors, float[] normals, int[] indices ) {
			this.positions = positions;
			this.colors = colors;
			this.normals = normals;
			this.indices = indices;
		}

		int vertexCount() { return positions.length / 3; }
		int triangleCount() { return indices.length / 3; }
	}

	private final File source;
	private final ParsedObj parsed;
	private transient Geometry geometry;

	VertexColorObjGen( String name, File source, ParsedObj parsed, Tweed tweed ) {
		super( name, tweed );
		if ( source == null || parsed == null )
			throw new IllegalArgumentException( "Vertex-colour OBJ source/data is missing" );
		this.source = source;
		this.parsed = parsed;
	}

	@Override
	public void calculate() {
		gNode.detachAllChildren();
		Mesh mesh = new Mesh();
		mesh.setMode( Mesh.Mode.Triangles );
		mesh.setBuffer( VertexBuffer.Type.Position, 3, BufferUtils.createFloatBuffer( parsed.positions ) );
		mesh.setBuffer( VertexBuffer.Type.Color, 4, BufferUtils.createFloatBuffer( parsed.colors ) );
		mesh.setBuffer( VertexBuffer.Type.Normal, 3, BufferUtils.createFloatBuffer( parsed.normals ) );
		// An int buffer forces 32-bit indices, including meshes with more than 65,535 vertices.
		mesh.setBuffer( VertexBuffer.Type.Index, 3, BufferUtils.createIntBuffer( parsed.indices ) );
		mesh.updateCounts();
		mesh.updateBound();

		Material material = new Material( tweed.getAssetManager(), "Common/MatDefs/Misc/Unshaded.j3md" );
		material.setBoolean( "VertexColor", true );
		geometry = new Geometry( name, mesh );
		geometry.setMaterial( material );
		geometry.setUserData( HandleMe.class.getSimpleName(), true );
		gNode.setUserData( HandleMe.class.getSimpleName(), true );
		gNode.attachChild( geometry );
		super.calculate();
	}

	@Override
	public JComponent getUI() {
		JPanel panel = new JPanel( new ListDownLayout() );
		panel.add( new JLabel( "OBJ vertex colour" ) );
		panel.add( new JLabel( parsed.vertexCount() + " vertices, " + parsed.triangleCount() + " triangles" ) );
		return panel;
	}

	@Override
	public void dumpObj( ObjDump dump ) {
		dump.addAll( new ObjRead( source ) );
	}

	static ParsedObj parse( File file ) throws IOException {
		if ( file == null || !file.isFile() )
			throw new IOException( "Vertex-colour OBJ is missing: " + file );

		List<float[]> vertices = new ArrayList<>();
		List<float[]> vertexColors = new ArrayList<>();
		List<Integer> triangles = new ArrayList<>();
		int lineNumber = 0;
		try ( BufferedReader reader = Files.newBufferedReader( file.toPath(), StandardCharsets.UTF_8 ) ) {
			String line;
			while ( ( line = reader.readLine() ) != null ) {
				lineNumber++;
				String trimmed = line.trim();
				if ( trimmed.isEmpty() || trimmed.startsWith( "#" ) )
					continue;
				String[] parts = trimmed.split( "\\s+" );
				if ( "v".equals( parts[ 0 ] ) ) {
					if ( parts.length < 7 )
						throw failure( file, lineNumber, "vertex has no RGB values" );
					float x = finite( parts[ 1 ], file, lineNumber );
					float y = finite( parts[ 2 ], file, lineNumber );
					float z = finite( parts[ 3 ], file, lineNumber );
					float r = finite( parts[ 4 ], file, lineNumber );
					float g = finite( parts[ 5 ], file, lineNumber );
					float b = finite( parts[ 6 ], file, lineNumber );
					float scale = Math.max( r, Math.max( g, b ) ) > 1f ? 1f / 255f : 1f;
					vertices.add( new float[] { x, y, z } );
					vertexColors.add( new float[] { clamp( r * scale ), clamp( g * scale ), clamp( b * scale ), 1f } );
				} else if ( "f".equals( parts[ 0 ] ) ) {
					if ( parts.length < 4 )
						throw failure( file, lineNumber, "face has fewer than three vertices" );
					int first = vertexIndex( parts[ 1 ], vertices.size(), file, lineNumber );
					int previous = vertexIndex( parts[ 2 ], vertices.size(), file, lineNumber );
					for ( int i = 3; i < parts.length; i++ ) {
						int current = vertexIndex( parts[ i ], vertices.size(), file, lineNumber );
						triangles.add( first );
						triangles.add( previous );
						triangles.add( current );
						previous = current;
					}
				}
			}
		} catch ( NumberFormatException error ) {
			throw failure( file, lineNumber, "invalid numeric value", error );
		}
		if ( vertices.isEmpty() || triangles.isEmpty() )
			throw new IOException( "Vertex-colour OBJ has no renderable mesh: " + file );

		float[] positions = new float[ vertices.size() * 3 ];
		float[] colors = new float[ vertices.size() * 4 ];
		for ( int i = 0; i < vertices.size(); i++ ) {
			System.arraycopy( vertices.get( i ), 0, positions, i * 3, 3 );
			System.arraycopy( vertexColors.get( i ), 0, colors, i * 4, 4 );
		}
		int[] indices = new int[ triangles.size() ];
		for ( int i = 0; i < triangles.size(); i++ )
			indices[ i ] = triangles.get( i );
		return new ParsedObj( positions, colors, normals( positions, indices ), indices );
	}

	private static int vertexIndex( String token, int vertexCount, File file, int line ) throws IOException {
		String raw = token;
		int slash = raw.indexOf( '/' );
		if ( slash >= 0 ) raw = raw.substring( 0, slash );
		if ( raw.isEmpty() ) throw failure( file, line, "face has an empty vertex index" );
		final int objIndex;
		try { objIndex = Integer.parseInt( raw ); }
		catch ( NumberFormatException error ) { throw failure( file, line, "invalid face vertex index", error ); }
		int index = objIndex > 0 ? objIndex - 1 : vertexCount + objIndex;
		if ( objIndex == 0 || index < 0 || index >= vertexCount )
			throw failure( file, line, "face vertex index is out of range" );
		return index;
	}

	private static float finite( String token, File file, int line ) throws IOException {
		float value;
		try { value = Float.parseFloat( token ); }
		catch ( NumberFormatException error ) { throw failure( file, line, "invalid numeric value", error ); }
		if ( Float.isNaN( value ) || Float.isInfinite( value ) )
			throw failure( file, line, "non-finite numeric value" );
		return value;
	}

	private static float clamp( float value ) { return Math.max( 0f, Math.min( 1f, value ) ); }

	private static float[] normals( float[] positions, int[] indices ) {
		float[] normals = new float[ positions.length ];
		for ( int i = 0; i < indices.length; i += 3 ) {
			int a = indices[ i ] * 3, b = indices[ i + 1 ] * 3, c = indices[ i + 2 ] * 3;
			float abx = positions[ b ] - positions[ a ], aby = positions[ b + 1 ] - positions[ a + 1 ], abz = positions[ b + 2 ] - positions[ a + 2 ];
			float acx = positions[ c ] - positions[ a ], acy = positions[ c + 1 ] - positions[ a + 1 ], acz = positions[ c + 2 ] - positions[ a + 2 ];
			float nx = aby * acz - abz * acy, ny = abz * acx - abx * acz, nz = abx * acy - aby * acx;
			for ( int vertex : new int[] { a, b, c } ) { normals[ vertex ] += nx; normals[ vertex + 1 ] += ny; normals[ vertex + 2 ] += nz; }
		}
		for ( int i = 0; i < normals.length; i += 3 ) {
			float length = (float) Math.sqrt( normals[ i ] * normals[ i ] + normals[ i + 1 ] * normals[ i + 1 ] + normals[ i + 2 ] * normals[ i + 2 ] );
			if ( length > 1e-12f ) { normals[ i ] /= length; normals[ i + 1 ] /= length; normals[ i + 2 ] /= length; }
			else normals[ i + 1 ] = 1f;
		}
		return normals;
	}

	private static IOException failure( File file, int line, String message ) { return failure( file, line, message, null ); }
	private static IOException failure( File file, int line, String message, Throwable cause ) {
		return new IOException( message + " at " + file + ":" + line, cause );
	}
}
